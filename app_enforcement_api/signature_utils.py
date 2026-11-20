import base64

PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
MAX_SIGNATURE_BYTES = 300_000
PREFIX = 'data:image/png;base64,'


def validate_png(raw, label='Signature'):
    """Check raw bytes are a PNG of acceptable size. Returns the bytes.
    Raises ValueError with a message safe to show the user."""
    if not raw.startswith(PNG_MAGIC):
        raise ValueError(f'Invalid {label.lower()} image.')
    if len(raw) > MAX_SIGNATURE_BYTES:
        raise ValueError(f'{label} is too large.')
    return raw


def decode_signature(data_url, label='Violator signature'):
    """Validate a PNG data URL and return the raw bytes.
    Raises ValueError with a message safe to show the user."""
    if not isinstance(data_url, str) or not data_url.startswith(PREFIX):
        raise ValueError(f'{label} is required.')
    try:
        raw = base64.b64decode(data_url[len(PREFIX):], validate=True)
    except Exception:
        raise ValueError(f'Invalid {label.lower()} image.')
    return validate_png(raw, label)
