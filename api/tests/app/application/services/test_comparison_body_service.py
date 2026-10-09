import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.scope import OwnerScope, Principal


@pytest.mark.asyncio
async def test_retained_input_pages_are_utf8_safe_and_sanitize_without_execution_at():
    from app.application.services.comparison_body_service import ComparisonBodyService

    body = '{"message":"你好你好","token":"secret"}'
    repository = SimpleNamespace(
        content_snapshot=AsyncMock(
            return_value={
                "body": body,
                "content_digest": hashlib.sha256(body.encode()).hexdigest(),
                "redacted": False,
                "content_id": "c",
            }
        ),
        current=AsyncMock(return_value="authority"),
    )
    service = ComparisonBodyService(repository, None, secret="long-secret-0123456789")
    scope = OwnerScope.personal("u")
    principal = Principal(user_id="u")
    page = await service.read(
        scope, principal, "comparison", 1, "run", "step", "input", limit_bytes=8
    )
    parts = [page.content]
    while page.next_cursor:
        page = await service.read(
            scope,
            principal,
            "comparison",
            1,
            "run",
            "step",
            "input",
            cursor=page.next_cursor,
            limit_bytes=8,
        )
        parts.append(page.content)
    assert "secret" not in "".join(parts)
    assert "你好你好" in "".join(parts)
    assert page.at is None


@pytest.mark.asyncio
async def test_retained_body_revocation_during_read_discards_every_byte():
    from app.application.services.comparison_body_service import ComparisonBodyService

    repository = SimpleNamespace(
        content_snapshot=AsyncMock(
            return_value={
                "body": "{}",
                "content_digest": hashlib.sha256(b"{}").hexdigest(),
                "redacted": False,
                "content_id": "c",
            }
        ),
        current=AsyncMock(side_effect=["old", "revoked"]),
    )
    service = ComparisonBodyService(repository, None, secret="long-secret-0123456789")
    with pytest.raises(PermissionError):
        await service.read(
            OwnerScope.personal("u"),
            Principal(user_id="u"),
            "comparison",
            1,
            "run",
            "step",
            "input",
        )


@pytest.mark.asyncio
async def test_redacted_retained_input_is_unreadable():
    from app.application.services.comparison_body_service import ComparisonBodyService

    repository = SimpleNamespace(
        content_snapshot=AsyncMock(
            return_value={
                "body": '{"secret":"never expose"}',
                "content_digest": "unused",
                "redacted": True,
                "content_id": "c",
            }
        ),
        current=AsyncMock(return_value="authority"),
    )
    service = ComparisonBodyService(repository, None, secret="long-secret-0123456789")
    result = await service.read(
        OwnerScope.personal("u"),
        Principal(user_id="u"),
        "comparison",
        1,
        "run",
        "step",
        "input",
    )
    assert result.availability == "unavailable"
    assert result.content is None
