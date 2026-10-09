"""Select immutable host-local image identities for explicit owned Docker tests."""

import os
import re


def evaluation_test_image(role: str, default: str) -> str:
    value = os.environ.get(f"E04_TEST_{role}_IMAGE", default)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("evaluation_test_image_requires_immutable_content_id")
    return value
