"""Single preparation path for a chat request, shared by the GTK window and the CLI.

Both frontends repeated the same pre-flight: default an image-only prompt,
enforce the local history limit, resolve the provider route, validate the
image destination and read the reviewed document excerpts. Only the approval
step differs (GTK dialogs versus CLI flags), so it is injected as a callback
and this module never imports GTK.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from .ai_client import AIProviderError
from .document_context import MAX_DOCUMENT_CONTEXT_CHARS
from .history_store import MAX_MESSAGE_CHARS
from .i18n import _


class RequestPreparationError(Exception):
    """Failure the caller shows to the user as-is; nothing is sent."""


@dataclass(frozen=True)
class PreparedRequest:
    """Everything both frontends need to send one reviewed request."""

    text: str
    images: tuple = ()
    document_context: str = ''
    provider: Optional[str] = None
    model: Optional[str] = None
    provider_ready: bool = False
    image_destination: Optional[Tuple[str, str]] = None


def prepare_request(text, *, ai_client, image=None, provider=None, model=None,
                    documents_enabled=False, resolve_route=True,
                    read_document_context=None, confirm_image=None, confirm_documents=None):
    """Validate one outgoing request and describe it, without sending anything.

    ``confirm_image(attachment, provider, model)`` and
    ``confirm_documents(context, provider)`` are optional approval hooks: they
    return False when the user declines, and the whole preparation then returns
    None. ``read_document_context(text, max_chars)`` reads the local index and
    is only called when documents are enabled. ``resolve_route=False`` skips
    the local readiness probe for callers that may answer locally before
    contacting a model.

    Returns None when there is nothing to send (empty prompt, declined review).
    Failures the user must see raise ``RequestPreparationError``.
    """
    text = (text or '').strip()
    if not text and image is not None:
        text = _("Describe this image.")
    if not text:
        return None
    if len(text) > MAX_MESSAGE_CHARS:
        raise RequestPreparationError(
            _("Message exceeds the local history limit. Shorten it before sending."))

    # Route resolution is local-only: no network request is made here. The
    # active provider names the route for diagnostics; readiness is asked with
    # the caller's own selection so lightweight clients only need that method.
    selected, ready = None, False
    if resolve_route:
        try:
            active_provider = getattr(ai_client, 'active_provider', None)
            selected = active_provider(provider) if callable(active_provider) else provider
        except AIProviderError:
            selected = None
        try:
            ready = bool(ai_client.provider_ready(provider))
        except AIProviderError:
            ready = False

    images = ()
    image_destination = None
    if image is not None:
        selected, model = ai_client.validate_image_request((image,), provider, model)
        if not ready:
            raise RequestPreparationError(
                _("Configure a usable OpenRouter provider before sending an image."))
        if confirm_image is not None and not confirm_image(image, selected, model):
            return None
        images = (image,)
        image_destination = (selected, model)
        ready = True

    document_context = ''
    if documents_enabled:
        if read_document_context is None:
            raise ValueError("Document selection requires a context reader")
        document_context = read_document_context(text, MAX_DOCUMENT_CONTEXT_CHARS)
        if not document_context:
            raise RequestPreparationError(
                _("No matching passages in the selected documents. "
                  "Add documents or refine your question."))
        if ready and confirm_documents is not None and not confirm_documents(document_context, selected):
            return None

    return PreparedRequest(
        text=text,
        images=images,
        document_context=document_context,
        provider=selected,
        model=model,
        provider_ready=ready,
        image_destination=image_destination,
    )
