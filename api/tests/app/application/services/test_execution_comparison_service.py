"""Application bounds and fresh authority are enforced before releasing retained data."""

from dataclasses import dataclass
from importlib.util import find_spec
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.ports.execution_analysis import AuthorityState

pytestmark = pytest.mark.asyncio


def implementation():
    assert find_spec("app.application.services.execution_comparison_service") is not None, (
        "comparison service missing"
    )
    from app.application.services.execution_comparison_service import ExecutionComparisonService

    return ExecutionComparisonService


@dataclass
class Principal:
    user_id: str = "user"
    is_auditor: bool = False


class Repository:
    def __init__(self):
        self.calls = []
        self.read_count = 0
        self.changed = True

    async def materialize(self, scope, principal, request, **kwargs):
        self.calls.append((request, kwargs))
        return str(uuid4()), 1

    async def read(self, scope, principal, comparison_id, revision, **kwargs):
        from app.application.ports.execution_comparison import ComparisonRead

        self.read_count += 1
        return ComparisonRead(
            {
                "comparison_id": comparison_id,
                "revision": revision,
                "member_count": 2 if self.read_count == 1 else 1,
                "coverage_changed": self.read_count > 1,
            },
            AuthorityState(self.read_count, "resources"),
        )

    async def current(self, scope, principal, comparison_id, revision):
        return AuthorityState(2 if self.changed else self.read_count, "resources")


def scope():
    return SimpleNamespace(user_id="user")


async def test_get_reloads_current_subset_after_revocation_without_releasing_old_count():
    repo = Repository()
    result = await implementation()(repo).get(scope(), Principal(), str(uuid4()), 1)
    assert result["member_count"] == 1
    assert result["coverage_changed"] is True
    assert "authority" not in result


async def test_unstable_authority_never_releases_last_stale_body():
    repo = Repository()

    async def always_changes(*args):
        return AuthorityState(99, "resources")

    repo.current = always_changes
    with pytest.raises(PermissionError, match="coverage_changed"):
        await implementation()(repo).get(scope(), Principal(), str(uuid4()), 1)


async def test_six_detail_runs_rejected_before_repository_access():
    repo = Repository()
    with pytest.raises(ValueError, match="detail_limit"):
        await implementation()(repo).get(
            scope(), Principal(), str(uuid4()), 1, detail_run_ids=[str(uuid4()) for _ in range(6)]
        )
    assert repo.read_count == 0


async def test_create_passes_fixed_selection_and_resolved_timezone():
    repo = Repository()
    repo.changed = False
    run = str(uuid4())
    await implementation()(repo, workspace_timezone="Asia/Shanghai").create(
        scope(),
        Principal(),
        {
            "run_ids": [run, run],
            "timezone": "UTC",
            "filters": {},
            "request_id": "command",
            "mode": "explicit",
        },
    )
    request, kwargs = repo.calls[0]
    assert request.run_ids == (run,)
    assert request.query.timezone == "Asia/Shanghai"
    assert request.query.end > request.query.start
    assert kwargs == {}


async def test_refresh_requires_cas_and_creates_new_membership_revision():
    repo = Repository()
    repo.changed = False
    identity = str(uuid4())
    await implementation()(repo).refresh(
        scope(),
        Principal(),
        identity,
        {
            "request_id": "command",
            "expected_revision": 3,
            "mode": "all_matching",
            "excluded_run_ids": [str(uuid4())],
        },
    )
    assert repo.calls[0][1] == {"comparison_id": identity, "expected_revision": 3}


async def test_client_cannot_supply_author_provenance_or_capture_replacement():
    repo = Repository()
    for field in ("author", "watermark", "owner_kind", "metrics", "source_set_ids"):
        with pytest.raises(ValueError, match="invalid_comparison_request"):
            await implementation()(repo).create(
                scope(),
                Principal(),
                {"request_id": "command", "mode": "all_matching", field: "forged"},
            )
    assert repo.calls == []


async def test_scope_mismatch_and_auditor_writes_rejected_before_materializing():
    repo = Repository()
    for principal in (Principal(user_id="other"), Principal(is_auditor=True)):
        with pytest.raises(PermissionError):
            await implementation()(repo).create(
                scope(), principal, {"request_id": "command", "mode": "all_matching"}
            )
    assert repo.calls == []


async def test_alignment_cas_and_author_remain_server_controlled():
    repo = Repository()
    repo.changed = False
    stored = []

    async def align(scope, principal, identity, revision, *, expected_revision, edits, request_id):
        if expected_revision != len(stored):
            raise ValueError("alignment_conflict")
        stored.append(
            {"author": principal.user_id, "edits": edits, "supersedes": expected_revision}
        )
        return len(stored)

    repo.align = align
    service = implementation()(repo)
    identity = str(uuid4())
    edit = {
        "left_run_id": str(uuid4()),
        "right_run_id": str(uuid4()),
        "left_step_id": "step1",
        "right_step_id": "step2",
        "action": "confirm",
    }
    await service.align(
        scope(),
        Principal(),
        identity,
        1,
        {"request_id": "command", "expected_revision": 0, "edits": [edit]},
    )
    assert stored[0]["author"] == "user"
    with pytest.raises(ValueError, match="alignment_conflict"):
        await service.align(
            scope(),
            Principal(),
            identity,
            1,
            {"request_id": "command", "expected_revision": 0, "edits": [edit]},
        )
    with pytest.raises(ValueError, match="invalid_comparison_alignment"):
        await service.align(
            scope(),
            Principal(),
            identity,
            1,
            {
                "request_id": "command",
                "expected_revision": 1,
                "edits": [{**edit, "author": "other"}],
            },
        )
    assert len(stored) == 1


async def test_detail_suggestions_consume_retained_proof_and_leave_aggregate_untouched():
    from app.application.ports.execution_comparison import ComparisonRead

    repo = Repository()
    repo.changed = False
    ids = [str(uuid4()) for _ in range(2)]

    async def read(*args, **kwargs):
        return ComparisonRead(
            {
                "metrics": {"run_count": 100000},
                "details": [
                    {
                        "run_id": run,
                        "availability": "available",
                        "body": {
                            "steps": [
                                {
                                    "run_id": run,
                                    "step_id": "s",
                                    "attempt_id": "a",
                                    "kind": "tool",
                                    "tool_name": "search",
                                }
                            ],
                            "semantic_evidence": [
                                {
                                    "step_id": "s",
                                    "attempt_id": "a",
                                    "case_revision": "case",
                                    "semantic_key": "slot",
                                    "semantic_source": "fixed_case",
                                    "tool_contract_revision": "v1",
                                }
                            ],
                        },
                    }
                    for run in ids
                ],
            },
            AuthorityState(0, "resources"),
        )

    repo.read = read
    result = await implementation()(repo).get(
        scope(), Principal(), str(uuid4()), 1, detail_run_ids=ids
    )
    assert result["metrics"] == {"run_count": 100000}
    assert result["suggestions"][0]["status"] == "suggested"
    assert result["suggestions"][0]["provenance"] == "fixed_case_semantic"


async def test_artifact_jobs_accept_only_fixed_member_step_version_selection():
    queued = []

    class Jobs:
        async def enqueue(self, *args, request_id):
            queued.append(args[-1])
            return {"job_id": "job"}

    repo = Repository()
    service_type = implementation()
    service = service_type(repo, jobs=Jobs())
    side = {"run_id": str(uuid4()), "step_id": "step", "artifact_id": "artifact", "version": 1}
    result = await service.artifact_diff(
        scope(),
        Principal(),
        str(uuid4()),
        1,
        {"request_id": "command", "left": side, "right": side, "format": "json"},
    )
    assert result == {"status": "queued", "job_id": "job"}
    with pytest.raises(ValueError, match="invalid_artifact_selection"):
        await service.artifact_diff(
            scope(),
            Principal(),
            str(uuid4()),
            1,
            {
                "request_id": "command",
                "left": {**side, "version": "latest"},
                "right": side,
                "format": "json",
            },
        )
    assert len(queued) == 1


async def test_mutations_require_request_id_and_preserve_stable_intent_fingerprint():
    from app.application.ports.execution_comparison import ComparisonRequest

    payload = {"mode": "explicit", "run_ids": [str(uuid4())]}
    with pytest.raises(ValueError, match="request_id"):
        ComparisonRequest.parse(payload)
    first = ComparisonRequest.parse({**payload, "request_id": "lost-response"})
    later = ComparisonRequest.parse({**payload, "request_id": "lost-response"})
    assert first.request_id == "lost-response"
    assert first.request_fingerprint == later.request_fingerprint
    assert first.query.end != later.query.end
    shifted_default = ComparisonRequest.parse(
        {**payload, "request_id": "lost-response"}, workspace_timezone="Asia/Shanghai"
    )
    assert shifted_default.request_fingerprint == first.request_fingerprint
    changed = ComparisonRequest.parse(
        {**payload, "request_id": "lost-response", "timezone": "Asia/Shanghai"}
    )
    assert first.request_fingerprint != changed.request_fingerprint


async def test_alignment_response_identifies_the_replayed_accepted_revision():
    repo = Repository()
    repo.changed = False

    async def align(*args, **kwargs):
        return 1  # receipt result, even if newer edits have happened since

    repo.align = align
    edit = {
        "left_run_id": str(uuid4()),
        "right_run_id": str(uuid4()),
        "left_step_id": "left",
        "right_step_id": "right",
        "action": "confirm",
    }
    result = await implementation()(repo).align(
        scope(),
        Principal(),
        str(uuid4()),
        1,
        {"request_id": "replayed", "expected_revision": 0, "edits": [edit]},
    )
    assert result["accepted_alignment_revision"] == 1
