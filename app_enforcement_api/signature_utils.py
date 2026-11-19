import base64
import uuid

from django.core.files.base import ContentFile

PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
MAX_SIGNATURE_BYTES = 500_000
PREFIX = 'data:image/png;base64,'


def save_violator_signature(ticket, data_url):
    """Decode a PNG data URL and store it on the ticket.
    Raises ValueError with a message safe to show the user."""
    if not isinstance(data_url, str) or not data_url.startswith(PREFIX):
        raise ValueError('Violator signature is required.')
    try:
        raw = base64.b64decode(data_url[len(PREFIX):], validate=True)
    except Exception:
        raise ValueError('Invalid signature image.')
    if not raw.startswith(PNG_MAGIC):
        raise ValueError('Invalid signature image.')
    if len(raw) > MAX_SIGNATURE_BYTES:
        raise ValueError('Signature is too large.')

    name = f'{ticket.transaction_code}_{uuid.uuid4().hex[:8]}.png'
    ticket.violator_signature.save(name, ContentFile(raw), save=False)
