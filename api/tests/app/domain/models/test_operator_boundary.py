from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.models.operator import (
    assert_operator_url_allowed,
    normalize_operator_domains,
)
from app.domain.models.scheduled_job import ScheduledJob
from app.domain.models.session import Session


def test_domains_are_canonical_exact_hosts():
    assert normalize_operator_domains(
        ["OPS-CONSOLE", "https://Example.COM/", "example.com:8443"]
    ) == ["ops-console", "example.com"]


@pytest.mark.parametrize(
    "value",
    ["*.example.com", "example.com/path", "user@example.com", "example.com?q=1"],
)
def test_domain_configuration_rejects_ambiguous_patterns(value):
    with pytest.raises(ValueError, match="host names"):
        normalize_operator_domains([value])


def test_operator_url_allowlist_is_exact_and_http_only():
    assert_operator_url_allowed("https://example.com/task", ["example.com"])

    with pytest.raises(PermissionError, match="not allowed"):
        assert_operator_url_allowed("https://sub.example.com/task", ["example.com"])
    with pytest.raises(ValueError, match="absolute HTTP"):
        assert_operator_url_allowed("file:///etc/passwd", ["example.com"])


def test_operator_session_requires_an_allowed_domain():
    with pytest.raises(ValidationError):
        Session(operator_scope="owned")


def test_operator_scheduled_job_requires_an_allowed_domain():
    with pytest.raises(ValidationError):
        ScheduledJob(
            name="operator",
            owner_user_id="user-1",
            prompt_template="run",
            operator_scope="third_party_saas",
            created_at=datetime.now(UTC),
        )
