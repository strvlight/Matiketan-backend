import base64

PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
MAX_SIGNATURE_BYTES = 300_000
PREFIX = 'data:image/png;base64,'


def decode_signature(data_url):
    """Validate a PNG data URL and return the raw bytes.
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
    return raw
