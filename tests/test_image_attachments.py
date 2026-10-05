"""Image preparation never touches a provider and preserves reviewed pixels."""

import io
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, PngImagePlugin

from src.image_attachments import (
    ImageAttachmentError, MAX_SOURCE_BYTES, MAX_IMAGE_EDGE,
    prepare_image, prepare_image_bytes, validate_attachment,
)


def png_bytes(size=(12, 8), color="red", metadata=None):
    output = io.BytesIO()
    with Image.new("RGB", size, color) as image:
        image.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


class ImagePreparationTests(unittest.TestCase):
    def test_strips_metadata_and_snapshot_is_immutable(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("private", "Synthetic location and owner")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "chosen.png"
            source.write_bytes(png_bytes(metadata=metadata))
            attachment = prepare_image(source)
            before = attachment.data
            source.write_bytes(png_bytes(color="blue"))
            self.assertEqual(attachment.data, before)
            self.assertNotEqual(prepare_image(source).data, before)
            with self.assertRaises(FrozenInstanceError):
                attachment.data = b"replacement"
        self.assertNotIn(b"Synthetic location", attachment.data)
        with Image.open(io.BytesIO(attachment.data)) as normalized:
            self.assertEqual(normalized.format, "JPEG")
            self.assertNotIn("private", normalized.info)
            self.assertNotIn("exif", normalized.info)
            self.assertNotIn("icc_profile", normalized.info)
        self.assertEqual(attachment.filename, "chosen.png")
        self.assertTrue(attachment.data_url.startswith("data:image/jpeg;base64,"))
        self.assertNotIn(attachment.data_url[23:70], repr(attachment))
        validate_attachment(attachment)

    def test_scales_source_before_encoding(self):
        attachment = prepare_image_bytes(png_bytes((3000, 1000)), "wide.png")
        self.assertEqual(attachment.width, MAX_IMAGE_EDGE)
        self.assertLessEqual(attachment.height, MAX_IMAGE_EDGE)
        self.assertAlmostEqual(attachment.width / attachment.height, 3, delta=.01)

    def test_transparency_is_composited_on_white(self):
        output = io.BytesIO()
        with Image.new("RGBA", (4, 4), (255, 0, 0, 0)) as image:
            image.save(output, format="PNG")
        attachment = prepare_image_bytes(output.getvalue())
        with Image.open(io.BytesIO(attachment.data)) as normalized:
            self.assertEqual(normalized.getpixel((0, 0)), (255, 255, 255))

    def test_corrupt_source_has_safe_error(self):
        with self.assertRaises(ImageAttachmentError) as caught:
            prepare_image_bytes(b"synthetic-secret-source", "/private/secret.png")
        self.assertNotIn("synthetic-secret", str(caught.exception))
        self.assertNotIn("/private", str(caught.exception))

    def test_source_byte_limit_checked_before_decoding(self):
        with patch("src.image_attachments.Image.open") as decoder:
            with self.assertRaises(ImageAttachmentError):
                prepare_image_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
            decoder.assert_not_called()

    def test_dimension_limit_checked_before_pixel_decode(self):
        source = Mock(format="PNG", size=(12001, 100), n_frames=1)
        source.__enter__ = Mock(return_value=source)
        source.__exit__ = Mock(return_value=False)
        with patch("src.image_attachments.Image.open", return_value=source):
            with self.assertRaisesRegex(ImageAttachmentError, "dimension"):
                prepare_image_bytes(b"synthetic-header")
        source.load.assert_not_called()

    def test_pixel_limit_checked_before_pixel_decode(self):
        source = Mock(format="PNG", size=(6000, 6000), n_frames=1)
        source.__enter__ = Mock(return_value=source)
        source.__exit__ = Mock(return_value=False)
        with patch("src.image_attachments.Image.open", return_value=source):
            with self.assertRaisesRegex(ImageAttachmentError, "32 MP"):
                prepare_image_bytes(b"synthetic-header")
        source.load.assert_not_called()

    def test_animated_and_other_image_formats_rejected(self):
        for format, frames in (("GIF", 1), ("WEBP", 2)):
            source = Mock(format=format, size=(10, 10), n_frames=frames)
            source.__enter__ = Mock(return_value=source)
            source.__exit__ = Mock(return_value=False)
            with patch("src.image_attachments.Image.open", return_value=source):
                with self.assertRaises(ImageAttachmentError):
                    prepare_image_bytes(b"synthetic-header")
            source.load.assert_not_called()

    def test_large_normalized_output_fails_without_returning_partial_attachment(self):
        with patch("src.image_attachments.MAX_IMAGE_BYTES", 8):
            with self.assertRaisesRegex(ImageAttachmentError, "Normalized"):
                prepare_image_bytes(png_bytes())

    def test_tampered_normalized_snapshot_is_rejected(self):
        attachment = prepare_image_bytes(png_bytes())
        for changed in (replace(attachment, data=b"not-jpeg"), replace(attachment, width=13)):
            with self.assertRaises(ImageAttachmentError):
                validate_attachment(changed)

    def test_symlink_directory_and_fifo_not_opened_as_image(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(png_bytes())
            link = Path(directory) / "link.png"
            link.symlink_to(source)
            fifo = Path(directory) / "fifo.png"
            os.mkfifo(fifo)
            for path in (link, fifo, Path(directory)):
                with self.subTest(kind=path.name):
                    with self.assertRaises(ImageAttachmentError):
                        prepare_image(path)

    def test_exif_orientation_is_applied_and_removed(self):
        output = io.BytesIO()
        with Image.new("RGB", (8, 4), "red") as image:
            exif = Image.Exif()
            exif[274] = 6
            exif[270] = "synthetic-private-metadata"
            image.save(output, format="JPEG", exif=exif)
        attachment = prepare_image_bytes(output.getvalue())
        self.assertEqual((attachment.width, attachment.height), (4, 8))
        with Image.open(io.BytesIO(attachment.data)) as normalized:
            self.assertFalse(normalized.getexif())
        self.assertNotIn(b"synthetic-private-metadata", attachment.data)


if __name__ == "__main__":
    unittest.main()
