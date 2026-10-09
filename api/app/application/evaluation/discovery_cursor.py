"""Signed bounded discovery tokens, bound to current caller and exact query."""

import base64
import hashlib
import hmac
import json


def encode(secret, context, position):
    if not secret or len(secret) < 16:
        raise ValueError("discovery_not_configured")
    payload = json.dumps([context, position], sort_keys=True, separators=(",", ":")).encode()
    return (
        base64.urlsafe_b64encode(payload + hmac.new(secret, payload, hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )


def decode(secret, context, cursor):
    try:
        if not secret or len(secret) < 16 or len(cursor) > 4096:
            raise ValueError()
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        payload, signature = raw[:-32], raw[-32:]
        if not hmac.compare_digest(signature, hmac.new(secret, payload, hashlib.sha256).digest()):
            raise ValueError()
        saved, position = json.loads(payload)
        if saved != context:
            raise ValueError()
        return position
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError("invalid_cursor") from error
