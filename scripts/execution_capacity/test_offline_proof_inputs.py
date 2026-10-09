"""Private replay inputs; real local journals only, no service clients."""

from uuid import uuid4

from scripts.execution_capacity.evidence_owner import EvidenceOwner
from scripts.execution_capacity.inventory_reader import ReadOnlyParents
from scripts.execution_capacity.observers import RecoveryJournal


def test_parent_reads_retain_original_body_receipt_absence_and_sequence(tmp_path):
    owner = EvidenceOwner()
    identity = str(uuid4())
    (tmp_path / "history").mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "history") as journal:
        journal.intent("run", identity, {"scope": "user:fixture", "raw": "private-body"})
        parents = ReadOnlyParents((journal,), budget=owner.budget.child(), evidence=owner)
        assert parents.parent("run", identity)["raw"] == "private-body"
        assert parents.get("activity", "missing") is None
        assert len(parents.records("run")) == 1
        records = owner.originals["journal-read"]
        assert [r["sequence"] for r in records] == [1, 2, 3]
        assert records[0]["kind"] == "run"
        assert records[0]["key"] == identity
        assert records[0]["value"]["receipt"] is None
        assert records[1]["kind"] == "activity"
        assert records[1]["value"] is None
        assert records[2]["operation"] == "records"
        assert records[2]["value"][0][0] == identity


def test_admission_replay_uses_original_hmac_and_content_identity():
    import pytest
    from scripts.execution_capacity import persistence

    from app.domain.models.execution_usage import content_revision
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_physical_requester_repository import (
        DBPhysicalRequesterRepository,
    )

    run_id = uuid4()
    scope = OwnerScope.personal("fixture")
    proof = {
        "version": 1,
        "scope": "user:fixture",
        "run_id": str(run_id),
        "kind": "user",
        "principal": {"user_id": "fixture"},
    }
    body = {
        "physical_requester": DBPhysicalRequesterRepository(
            None, signing_secret="fixture-secret"
        )._seal(proof)
    }
    row = {
        "body": body,
        "purpose": "production",
        "id": content_revision({"run_id": str(run_id), "purpose": "production", "body": body}),
    }
    assert persistence.verify_admission_configuration(
        [row], scope=scope, run_id=run_id, purpose="production", signing_secret="fixture-secret"
    ) == str(row["id"])
    for secret in ("wrong-secret", ""):
        with pytest.raises(ValueError, match=r".+"):
            persistence.verify_admission_configuration(
                [row], scope=scope, run_id=run_id, purpose="production", signing_secret=secret
            )


def test_retained_playback_checkpoint_empty_suffix_rechecks_actual_boundary():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import pytest

    from app.domain.models.playback import PlaybackBoundary
    from app.infrastructure.execution import postgres_playback as playback

    boundary = PlaybackBoundary(
        run_id=uuid4(),
        formal_position=1,
        progress_position=0,
        observed_order=1,
        projection_revision=1,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        projector_version=1,
    )
    actual = SimpleNamespace(
        formal_position=1,
        progress_position=0,
        projection_revision=1,
        observed_at=boundary.observed_at,
    )
    playback.check_playback_boundary(actual, boundary)
    checkpoint = SimpleNamespace(
        observed_order=1,
        state_ref={
            "schema_version": 1,
            "projector_version": 1,
            "state": {"run": {str(boundary.run_id): {"status": "completed"}}},
        },
    )
    prefix = playback.playback_prefix(
        boundary, SimpleNamespace(completeness={}), checkpoint, [[]], [1]
    )
    result = playback.finish_playback(boundary, prefix, [])
    assert result.state["run"][str(boundary.run_id)]["status"] == "completed"
    assert result.missing_intervals == ()
    actual.formal_position = 2
    with pytest.raises(playback.PlaybackUnavailable, match="persisted journal cut"):
        playback.check_playback_boundary(actual, boundary)


def test_run_input_bundle_retains_exact_read_ranges_and_failure():
    import pytest

    owner = EvidenceOwner()
    with (  # noqa: PT012 - the actual retained prefix must precede the failure
        pytest.raises(RuntimeError, match="private failure"),
        owner.run_inputs(run_id="run", scope="user:fixture", kind="standard", objects=[]),
    ):
        owner.retain("signed-configuration", [])
        raise RuntimeError("private failure")
    record = owner.originals["run-input"][0]
    assert record["identity"] == {"run_id": "run", "scope": "user:fixture", "kind": "standard"}
    assert record["ranges"]["signed-configuration"] == [0, 1]
    assert record["ranges"]["event-source"] == [0, 0]
    assert record["sql"] == [0, 0]
    assert record["objects"] == [0, 0]
    assert record["error"] == "RuntimeError"
    assert "private failure" not in str(record)


def test_original_version_authorization_rejects_revoked_principal():
    from types import SimpleNamespace

    import pytest

    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories import db_evaluation_dataset_repository as repository

    scope = OwnerScope.personal("fixture")
    principal = SimpleNamespace(
        user_id="fixture", is_auditor=False, token_version=2, global_role="user", team_roles={}
    )
    user = {"status": "active", "token_version": 2, "global_role": "user"}
    repository.authorize_original_user(scope, principal, write=False, user=user)
    with pytest.raises(PermissionError):
        repository.authorize_original_user(
            scope, principal, write=False, user={**user, "token_version": 1}
        )


def test_repository_operand_requires_source_result_on_private_session():
    from types import SimpleNamespace

    import pytest

    from app.infrastructure.execution.original_evidence import _EVIDENCE_KEY, retain_read

    owner = EvidenceOwner()
    first = {"uow": 1, "snapshot": "one"}
    other = {"uow": 2, "snapshot": "two"}
    first_token = owner.begin_sql(first)
    owner.complete_sql(first_token, first)
    other_token = owner.begin_sql(other)
    owner.complete_sql(other_token, other)
    session = SimpleNamespace(
        _original_evidence_owner=owner,
        evidence=owner,
        info={_EVIDENCE_KEY: owner},
        latest_sql=lambda: (other_token, other),
    )
    with pytest.raises(ValueError, match="source Result required"):
        retain_read(session, "version-source", "fixture", {"id": "one"}, None)
    assert owner.originals["version-source"] == []
    retain_read(SimpleNamespace(info={}), "version-source", "fixture", {"id": "one"}, None)
    assert owner.originals["version-source"] == []


def test_original_registered_body_checks_digest_and_revision():
    import pytest

    from app.domain.evaluation.configuration import digest
    from app.domain.evaluation.errors import DatasetNotFound
    from app.infrastructure.repositories import db_evaluation_environment_repository as repo

    body = {
        "id": str(uuid4()),
        "physical_resource": "fixture",
        "kind": "http",
        "endpoint": "https://fixture.invalid",
    }
    row = {"revision": 1, "body": body, "digest": digest(body)}
    assert str(repo.registered_original(row, "target", None, current=True).id) == body["id"]
    with pytest.raises(DatasetNotFound, match="environment_registry_unavailable"):
        repo.registered_original({**row, "digest": "bad"}, "target", None, current=True)
    with pytest.raises(DatasetNotFound, match="environment_registry_unavailable"):
        repo.registered_original(row, "target", 2, current=True)
