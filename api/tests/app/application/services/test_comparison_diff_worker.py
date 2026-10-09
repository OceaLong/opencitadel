"""Worker authorizes saved caller around I/O, and publishes only under its owned lease."""

from importlib.util import find_spec
from types import SimpleNamespace

import pytest

from app.domain.models.scope import OwnerScope, Principal

pytestmark = pytest.mark.asyncio


class Jobs:
    def __init__(self):
        self.published = []
        self.failed = []
        self.claim_open = False

    async def claim(self):
        assert not self.claim_open
        return {
            "id": "job",
            "lease_token": "lease",
            "principal": Principal(user_id="user").model_dump(mode="json"),
            "scope": OwnerScope.personal("user").model_dump(mode="json"),
            "comparison_id": "comparison",
            "revision": 1,
            "selection": {"left": {}, "right": {}, "format": "text"},
        }

    async def publish(self, job, scope, principal, result):
        self.published.append(result)

    async def fail(self, job):
        self.failed.append(job["id"])


class Reader:
    def __init__(self, fail=False):
        self.fail = fail

    async def read(self, *args, **kwargs):
        if self.fail:
            raise PermissionError("revoked")
        return SimpleNamespace(
            data=b"text",
            kind="doc",
            truncated=False,
            verified_digest="sha256:digest",
            public_metadata={"artifact_id": "a", "version": 1, "truncated": False},
        )


class Compute:
    async def compute(self, *args):
        return {
            "content_changed": False,
            "complete": True,
            "reason": None,
            "content": "",
            "operations": [],
        }


def worker(jobs, reader):
    assert find_spec("app.application.services.comparison_diff_worker") is not None, (
        "durable comparison diff worker missing"
    )
    from app.application.services.comparison_diff_worker import ComparisonDiffWorker

    return ComparisonDiffWorker(jobs, lambda scope, principal: reader, Compute())


async def test_current_authority_failure_never_publishes_cached_or_partial_body():
    jobs = Jobs()
    assert await worker(jobs, Reader(True)).process_pending() == 1
    assert jobs.published == []
    assert jobs.failed == ["job"]


async def test_full_input_result_is_published_with_both_fixed_metadata():
    jobs = Jobs()
    assert await worker(jobs, Reader()).process_pending() == 1
    assert jobs.published[0]["diff"]["complete"] is True
    assert set(jobs.published[0]) == {"left", "right", "format", "diff"}


async def test_truncated_input_cannot_claim_equality_or_complete_comparison():
    jobs = Jobs()
    reader = Reader()

    async def read(*args, **kwargs):
        return SimpleNamespace(
            data=b"prefix",
            kind="doc",
            truncated=True,
            verified_digest=None,
            public_metadata={"artifact_id": "a", "version": 1, "truncated": True},
        )

    reader.read = read
    await worker(jobs, reader).process_pending()
    result = jobs.published[0]["diff"]
    assert result["content_changed"] is None
    assert result["complete"] is False
    assert result["reason"] == "input_limit"


async def test_unknown_sdk_failure_marks_failed_and_never_publishes():
    jobs = Jobs()

    class FailingReader:
        async def read(self, *args, **kwargs):
            raise RuntimeError("provider read failed")

    assert await worker(jobs, FailingReader()).process_pending() == 1
    assert jobs.failed == ["job"]
    assert jobs.published == []


async def test_cancellation_keeps_lease_for_recovery_and_propagates():
    import asyncio

    jobs = Jobs()

    class CancelledReader:
        async def read(self, *args, **kwargs):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await worker(jobs, CancelledReader()).process_pending()
    assert jobs.failed == []
    assert jobs.published == []


async def test_revocation_at_publication_does_not_retry_old_result():
    jobs = Jobs()

    async def revoked(*args):
        raise PermissionError("current resource revoked after I/O")

    jobs.publish = revoked
    await worker(jobs, Reader()).process_pending()
    assert jobs.failed == ["job"]
    assert jobs.published == []
