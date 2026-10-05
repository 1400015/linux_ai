"""Bounded, metadata-free image snapshots for explicitly reviewed requests.

The original file is read once. Requests use immutable normalized bytes, so a
later file replacement cannot change the image the user previewed.
"""

import base64
import io
import os
import stat
import warnings
from dataclasses import dataclass, field
from typing import Union

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_SOURCE_BYTES = 12 * 1024 * 1024
MAX_SOURCE_PIXELS = 32 * 1000 * 1000
MAX_SOURCE_EDGE = 12000
MAX_IMAGE_EDGE = 2048
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGES_PER_REQUEST = 1

# Conservative, explicit capability list. Unknown IDs remain text-only here;
# adding a model requires confirming its image-input contract first.
OPENROUTER_IMAGE_MODELS = frozenset({
    "openai/gpt-4o", "openai/gpt-4o-mini", "openai/gpt-4.1",
    "openai/gpt-4.1-mini", "openai/gpt-4.1-nano", "openai/gpt-5",
    "openai/gpt-5-mini", "openai/gpt-5-nano",
    "anthropic/claude-3-haiku", "anthropic/claude-3.5-haiku",
    "anthropic/claude-3.5-sonnet", "anthropic/claude-3.7-sonnet",
    "anthropic/claude-sonnet-4", "anthropic/claude-sonnet-4.5",
    "anthropic/claude-opus-4", "anthropic/claude-opus-4.1",
    "anthropic/claude-opus-4.5", "anthropic/claude-haiku-4.5",
    "google/gemini-2.0-flash-001", "google/gemini-2.5-flash",
    "google/gemini-2.5-flash-lite", "google/gemini-2.5-pro",
    "google/gemini-3-flash-preview", "google/gemini-3-pro-preview",
})


class ImageAttachmentError(ValueError):
    """Invalid image, with a message that contains no local path or pixels."""


def supports_image_input(provider, model):
    return provider == "openrouter" and model in OPENROUTER_IMAGE_MODELS


def _display_name(filename):
    # A name is only a local UI label. It is never included in the API payload.
    name = os.path.basename(str(filename)).replace("\\", "_")
    name = "".join(char for char in name if char.isprintable())[:200]
    return name or "image"


@dataclass(frozen=True)
class ImageAttachment:
    filename: str
    width: int
    height: int
    mime_type: str
    data: bytes = field(repr=False)

    def __post_init__(self):
        if (not isinstance(self.data, bytes) or not self.data or len(self.data) > MAX_IMAGE_BYTES
                or self.mime_type != "image/jpeg" or type(self.width) is not int
                or type(self.height) is not int or not 0 < self.width <= MAX_IMAGE_EDGE
                or not 0 < self.height <= MAX_IMAGE_EDGE):
            raise ImageAttachmentError("Invalid normalized image attachment")
        object.__setattr__(self, "filename", _display_name(self.filename))

    @property
    def data_url(self):
        return "data:" + self.mime_type + ";base64," + base64.b64encode(self.data).decode("ascii")


class _BoundedImageBuffer(io.BytesIO):
    def write(self, value):
        if self.tell() + len(value) > MAX_IMAGE_BYTES:
            raise ImageAttachmentError("Normalized image exceeds the 4 MiB limit")
        return super().write(value)


def prepare_image_bytes(data: bytes, filename: str = "capture.png") -> ImageAttachment:
    """Decode PNG/JPEG/WebP and create an immutable, metadata-free JPEG."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_SOURCE_BYTES:
        raise ImageAttachmentError("Image source exceeds the 12 MiB limit or is empty")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ImageAttachmentError("Choose a PNG, JPEG or WebP image")
                width, height = source.size
                if (width <= 0 or height <= 0 or width > MAX_SOURCE_EDGE or height > MAX_SOURCE_EDGE
                        or width * height > MAX_SOURCE_PIXELS):
                    raise ImageAttachmentError("Image exceeds the source dimension or 32 MP limit")
                if getattr(source, "n_frames", 1) != 1:
                    raise ImageAttachmentError("Animated images are not supported")
                source.load()
                oriented = ImageOps.exif_transpose(source)
                oriented.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
                # New RGB storage strips EXIF, PNG text, ICC and all other
                # metadata. Transparent pixels are composited on white.
                normalized = Image.new("RGB", oriented.size, "white")
                if "A" in oriented.getbands() or oriented.mode == "P":
                    rgba = oriented.convert("RGBA")
                    normalized.paste(rgba, mask=rgba.getchannel("A"))
                    rgba.close()
                else:
                    normalized.paste(oriented.convert("RGB"))
                output = _BoundedImageBuffer()
                try:
                    normalized.save(output, format="JPEG", quality=90)
                    return ImageAttachment(_display_name(filename), normalized.width, normalized.height,
                                           "image/jpeg", output.getvalue())
                finally:
                    output.close()
                    normalized.close()
                    oriented.close()
    except ImageAttachmentError:
        raise
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageAttachmentError("Image could not be decoded safely") from None


def prepare_image(path: Union[str, os.PathLike]) -> ImageAttachment:
    """Read one regular, non-symlink file with a source-byte limit."""
    descriptor = None
    try:
        descriptor = os.open(os.fspath(path), os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_SOURCE_BYTES:
            raise ImageAttachmentError("Choose a regular image file no larger than 12 MiB")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            data = source.read(MAX_SOURCE_BYTES + 1)
        return prepare_image_bytes(data, os.path.basename(os.fspath(path)))
    except ImageAttachmentError:
        raise
    except (OSError, TypeError, ValueError):
        raise ImageAttachmentError("Image file could not be read safely") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def validate_attachment(attachment):
    """Reject unprepared or inconsistent snapshots before creating a payload."""
    if not isinstance(attachment, ImageAttachment):
        raise ImageAttachmentError("Prepare and review the image before sending it")
    attachment.__post_init__()
    try:
        with Image.open(io.BytesIO(attachment.data)) as image:
            if image.format != "JPEG" or image.size != (attachment.width, attachment.height):
                raise ImageAttachmentError("Invalid normalized image attachment")
            if image.info.get("exif") or image.info.get("icc_profile") or image.info.get("comment"):
                raise ImageAttachmentError("Image must be normalized before review")
            image.load()
    except ImageAttachmentError:
        raise
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError):
        raise ImageAttachmentError("Invalid normalized image attachment") from None
