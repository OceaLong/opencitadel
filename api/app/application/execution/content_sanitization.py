"""Versioned public-body sanitization shared by immutable capture and safe reads."""

import json
import re

from app.application.execution.view_facts import safe_text
from app.domain.models.resource_pin import ResourceUnavailable

PUBLIC_CONTENT_POLICY = 2

_SECRET_KEY = re.compile(
    r"password|passwd|secret|token|api.?key|authorization|credential|private|storage|(?:^|_)(?:input|result|history)_refs?|policy_snapshot|presigned",
    re.IGNORECASE,
)

_PRIVATE_OBJECT = re.compile(r"execution/(?:inputs|results)/[^\s\"'<>]+", re.IGNORECASE)
_QUOTED_CREDENTIAL = re.compile(
    r"""(?i)(["'](?:password|passwd|secret|token|api[_-]?key|authorization|credential|access_token|refresh_token)["']\s*:\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""
)


def sanitize_content(value, depth=0):
    if depth > 64:
        raise ResourceUnavailable("content nesting exceeds supported depth")
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if _SECRET_KEY.search(str(key))
            else sanitize_content(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_content(item, depth + 1) for item in value]
    if isinstance(value, str):
        # Results commonly contain serialized JSON. Preserve the string type,
        # but apply the same nested field contract before free-text scanning.
        if value.lstrip().startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                cleaned = sanitize_content(decoded, depth + 1)
                if cleaned != decoded:
                    value = json.dumps(cleaned, ensure_ascii=False)
        value = _QUOTED_CREDENTIAL.sub(lambda match: match[1] + '"[redacted]"', value)
        value = _PRIVATE_OBJECT.sub("[redacted]", value)
        return safe_text(value, limit=len(value))
    if value is None or type(value) in (bool, int, float):
        return value
    raise ResourceUnavailable("unsupported content value")
