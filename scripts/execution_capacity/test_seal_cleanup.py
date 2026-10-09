"""Real observer service constructors, plus actual seed drain/parent-exit join."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("missing_user", [False, True])
def test_actual_published_read_service_constructors(monkeypatch, missing_user):
    from scripts.execution_capacity.seal_cleanup import version_services

    from app.composition import uow
    from core.config import DeploymentSettings

    class Work:
        user = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get_by_id(self, user_id):
            return (
                None
                if missing_user
                else SimpleNamespace(
                    id=user_id, is_active=True, global_role="user", token_version=1
                )
            )

    work = Work()
    work.user = work
    captured = []

    def factory(**kwargs):
        captured.append(kwargs)
        return lambda authorization: work

    monkeypatch.setattr(uow, "create_uow_factory", factory)
    from scripts.execution_capacity.evidence_objects import EvidenceObjects
    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    owner = EvidenceOwner()
    resources = SimpleNamespace(
        evidence=owner,
        evidence_objects=EvidenceObjects(object(), owner.budget),
        settings=DeploymentSettings(_env_file=None, env="test"),
        postgres=SimpleNamespace(session_factory=object()),
        object_storage_client=object(),
    )
    binding = {"principal_id": "user-1", "probe": {"principal_id": "user-2"}}
    if missing_user:
        with pytest.raises(ValueError, match="principal"):
            asyncio.run(version_services(resources, binding))
    else:
        services = asyncio.run(version_services(resources, binding))
        assert set(services) == {"user:user-1", "user:user-2"}
        assert type(services["user:user-1"].batches).__name__ == "BatchService"
        assert type(services["user:user-1"].datasets).__name__ == "DatasetService"
        assert captured[0]["session_factory"] is resources.postgres.session_factory


@pytest.mark.parametrize("parent_complete", [True, False])
def test_seed_drain_awaits_real_parent_exit_and_closed_journals(
    tmp_path, monkeypatch, parent_complete
):
    from scripts.execution_capacity import seal_cleanup as cleanup
    from scripts.execution_capacity.guest_seal import incarnation, write_private
    from scripts.execution_capacity.test_guest_seal import row

    tmp_path.chmod(0o700)
    actual = row()
    ready = {
        "identity": {"attempt_id": "seed", "source_digest": "a" * 64},
        "deadline_ns": time.monotonic_ns() + 1_000_000_000,
    }
    write_private(tmp_path / "seal-ready.json", ready)
    handoff = {"parent": {"pid": 1}, "ready": ready}
    write_private(tmp_path / "seal-parent-ready.json", handoff)
    events = []

    class Poll:
        def register(self, pidfd, mask):
            assert pidfd == 51

        def poll(self, timeout):
            events.append("parent-exit")
            if parent_complete:
                write_private(
                    tmp_path / "seal-parent-done.json", {**handoff, "journals_closed": True}
                )
            return [51]

    monkeypatch.setattr(cleanup.select, "poll", Poll)

    def docker(*args):
        assert (tmp_path / "seal-release.json").exists()
        events.append("child-drained")
        actual["State"].update(Running=False, Status="exited", Pid=0, FinishedAt="finish")
        write_private(
            tmp_path / "historical-result.json",
            {
                "status": "corpus_ready",
                "attempt_id": "seed",
                "source_sha256": "a" * 64,
                "converged": True,
            },
        )
        return json.dumps([actual]).encode()

    monkeypatch.setattr(cleanup, "docker", docker)
    drain = cleanup.SeedDrain(tmp_path, ready, actual["Id"], incarnation(actual), 51)
    if parent_complete:
        asyncio.run(drain.finish())
        assert events == ["child-drained", "parent-exit"]
    else:
        with pytest.raises(FileNotFoundError):
            asyncio.run(drain.finish())
