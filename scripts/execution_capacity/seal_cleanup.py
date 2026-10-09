"""Fixed base observer composition, independent of every stopped producer.

This module never calls normal Postgres.init or writer factories. Existing
published-version services receive readonly UOWs; no scheduling lanes are built.
"""

import asyncio
import os
import select
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

from scripts.execution_capacity.attempt import digest
from scripts.execution_capacity.cumulative_cleanup import quiesce
from scripts.execution_capacity.final_inventory import FinalInventory
from scripts.execution_capacity.guest_seal import (
    incarnation,
    live_storage,
    read_private,
    verify_incarnation,
    write_private,
)
from scripts.execution_capacity.host import OwnedDeployment, docker, read_binding
from scripts.execution_capacity.inventory_reader import ReadOnlyParents
from scripts.execution_capacity.observer_resources import open_observers
from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal, RecoveryJournal
from scripts.execution_capacity.seal_handoff import release
from scripts.execution_capacity.writer_base import export_writer_base
from scripts.execution_capacity.writer_lifecycle import PROCESS_READ, ContainerWriters


async def version_services(resources, binding):
    from app.application.evaluation.batch_service import BatchService
    from app.application.evaluation.dataset_service import DatasetService
    from app.application.evaluation.environment_service import EnvironmentService
    from app.application.evaluation.suite_service import SuiteService
    from app.composition.uow import DBUnitOfWorkDependencies, create_uow_factory
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.adapters.security_ports import FernetVersionedSecretCipherAdapter
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher

    settings = resources.settings
    cipher = ApiKeyCipher(
        settings.api_key_secret,
        key_id=settings.api_key_secret_id,
        previous_secrets=settings.api_key_previous_secrets,
    )
    uow = create_uow_factory(
        session_factory=resources.postgres.session_factory,
        dependencies=DBUnitOfWorkDependencies(
            secret_cipher=FernetVersionedSecretCipherAdapter(cipher),
            audit_signing_key=settings.audit_signing_key,
            audit_signing_key_id=settings.audit_signing_key_id,
            database_authorization_signing_secret=settings.database_authorization_signing_secret,
        ),
    )
    objects = resources.evidence_objects
    cursor = (settings.public_cursor_secret or settings.api_key_secret).encode()
    # These exact read methods require no publisher, scheduler, policy mutation
    # or object-intent writer. SQL sessions enforce readonly even on misuse.
    datasets = DatasetService(uow, objects, None, cursor_secret=cursor)
    suites = SuiteService(uow, datasets, limits=None, policies=None, cursor_secret=cursor)
    environments = EnvironmentService(uow, None, cursor_secret=cursor)
    batches = BatchService(suites, preflight_factory=None)
    services = {}
    async with uow(AuthorizationContext.system("execution-kernel")) as work:
        for user_id in {binding["principal_id"], binding["probe"]["principal_id"]}:
            user = await work.user.get_by_id(user_id)
            principal = observer_principal(user, user_id)
            services["user:" + user_id] = SimpleNamespace(
                scope=OwnerScope.personal(user_id),
                principal=principal,
                datasets=datasets,
                suites=suites,
                environments=environments,
                batches=batches,
            )
    return services


def observer_principal(user, user_id):
    from app.domain.models.scope import Principal

    if user is None or not user.is_active or user.id != user_id:
        raise ValueError("actual observer principal unavailable")
    return Principal(
        user_id=user.id, global_role=user.global_role, token_version=user.token_version
    )


class SeedDrain:
    def __init__(self, root, ready, child, original, parent_fd, *, budget=None):
        self.root, self.ready, self.child, self.original = root, ready, child, original
        self.parent_fd = parent_fd
        self.budget = budget

    async def finish(self):
        import json

        release(self.root, self.ready["identity"], now_ns=time.monotonic_ns())
        # Normal child finally drains supervisor/resources and writes its receipt.
        # Do not signal it or infer exit from the release/receipt alone.
        while time.monotonic_ns() < self.ready["deadline_ns"]:
            row = json.loads(await asyncio.to_thread(docker, "container", "inspect", self.child))[0]
            verify_incarnation(row, self.original)
            if not row["State"]["Running"]:
                verify_incarnation(row, self.original, exited=True)
                result = read_private(self.root / "historical-result.json", budget=self.budget)
                if (
                    result.get("status") != "corpus_ready"
                    or result.get("attempt_id") != self.ready["identity"]["attempt_id"]
                    or result.get("source_sha256") != self.ready["identity"]["source_digest"]
                    or result.get("converged") is not True
                ):
                    raise ValueError("original seed normal completion differs")
                poll = select.poll()
                poll.register(self.parent_fd, select.POLLIN)
                while not poll.poll(0):
                    if time.monotonic_ns() >= self.ready["deadline_ns"]:
                        raise TimeoutError("seed parent still owns private journals")
                    await asyncio.sleep(0.05)
                done = read_private(self.root / "seal-parent-done.json", budget=self.budget)
                handoff = read_private(self.root / "seal-parent-ready.json", budget=self.budget)
                if done != {
                    "parent": handoff["parent"],
                    "ready": self.ready,
                    "journals_closed": True,
                }:
                    raise ValueError("seed parent normal completion missing")
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("seed normal drain did not finish by fixed deadline")


async def base_cleanup(config, ledger, manifest):
    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    root = Path(config["evidence_root"])
    with EvidenceOwner.from_limits(root, config.get("evidence_limits")) as evidence_owner:
        ledger.evidence_owner = evidence_owner
        evidence_owner.begin_cleanup()
        try:
            return await _base_cleanup(config, ledger, manifest, evidence_owner)
        except BaseException as error:
            if not (root / "c2c-failed-originals.json").exists():
                try:
                    evidence_owner.retain_failure(root, error)
                except BaseException as retention_error:  # noqa: BLE001 - preserve both acquisition and durable-retention failures
                    raise BaseExceptionGroup(
                        "original acquisition and prefix retention failed", [error, retention_error]
                    ) from None
            raise


async def _base_cleanup(config, ledger, manifest, evidence_owner):
    import json

    from scripts.acceptance.capacity_models import SourceOrigin
    from scripts.execution_capacity.guest_bridge import inspect_owned, process_snapshot

    from app.domain.models.authorization import AuthorizationContext
    from core.config import DeploymentSettings

    root, seed_root = Path(config["evidence_root"]), Path(config["seed_root"])
    bound = time.monotonic_ns() + config["phase_timeout_seconds"] * 1_000_000_000
    while not (seed_root / "seal-parent-ready.json").exists():
        if time.monotonic_ns() >= bound:
            raise TimeoutError("original seed readiness missing")
        await asyncio.sleep(0.05)
    handoff = read_private(seed_root / "seal-parent-ready.json", budget=evidence_owner.budget)
    ready = read_private(seed_root / "seal-ready.json", budget=evidence_owner.budget)
    if ready != handoff["ready"]:
        raise ValueError("seed parent readiness differs")
    identity = ready["identity"]
    if (
        identity["boot_id"] != ledger.plan["identity"]["boot_id"]
        or identity["source_digest"] != config["identity"]["source_digest"]
        or time.monotonic_ns() >= ready["deadline_ns"]
    ):
        raise ValueError("actual seed ready boot/source/deadline differs")
    binding = read_binding(Path(config["binding_path"]), budget=evidence_owner.budget)
    if (
        not binding.get("seal")
        or binding["writer_journal_root"] != config["writer_root"]
        or binding["source_sha256"] != identity["source_digest"]
    ):
        raise ValueError("fixed capacity seal binding missing")
    with ReadOnlyRecoveryJournal(seed_root, budget=evidence_owner.budget.child()) as original:
        if original.parent("attempt", identity["attempt_id"]) != {
            "invocation": binding["invocation"],
            "fixture_id": binding["fixture_id"],
            "source_sha256": binding["source_sha256"],
        }:
            raise ValueError("original seed attempt authority differs")
        deployment = OwnedDeployment(
            binding, ReadOnlyParents((original,), budget=evidence_owner.budget.child())
        )
        captures = dict(original.records("writer_process_capture"))
    # All original manifest members still bind config, even after prior normal
    # producer exit. Newly created children join through committed original
    # child intent + actual deployment inspection, never caller IDs alone.
    for expected in manifest["containers"]:
        inspect_owned(expected, running=None)
    _, rows = deployment.verify()
    if set(docker("ps", "--all", "--quiet", "--no-trunc").decode().split()) != set(rows):
        raise ValueError("dedicated seal guest has unknown containers")
    if (
        set(config["services"]) != {"postgres", "redis", "minio", "broker"}
        or len(set(config["services"].values())) != 4
    ):
        raise ValueError("exact persistent service role set required")
    for role, key in config["services"].items():
        expected = "opencitadel-sandbox-broker" if role == "broker" else role
        if binding["containers"][key]["service"] != expected or not rows[key]["State"]["Running"]:
            raise ValueError("actual persistent service role differs")
    if set(config["journal_roots"]) != {str(seed_root), config["writer_root"]}:
        raise ValueError("base requires complete original seed and writer journals")
    matches = [
        key
        for key in deployment.children
        if rows[key]["Config"]["Hostname"] == identity["hostname"] and rows[key]["State"]["Running"]
    ]
    if len(matches) != 1:
        raise ValueError("exact live seed child missing")
    child = matches[0]
    verify_incarnation(rows[child], handoff["child"])
    observed = json.loads(docker("exec", child, "python", "-c", PROCESS_READ, str(identity["pid"])))
    if any(observed[k] != identity[k] for k in ("pid", "start_ticks", "pid_namespace", "boot_id")):
        raise ValueError("seed process incarnation differs before capture")
    writers = ContainerWriters(
        deployment,
        docker,
        journal_root=Path(config["writer_root"]),
        prior_captures=captures,
        budget=evidence_owner.budget.child(),
    )
    originals = {key: incarnation(row) for key, row in rows.items()}
    processes = {
        key: process_snapshot(row["State"]["Pid"])
        for key, row in rows.items()
        if row["State"]["Running"]
    }
    storage = live_storage(config, rows)
    build_roles = dict(config["build_roles"])
    if set(build_roles) != {c["id"] for c in manifest["containers"]}:
        raise ValueError("all original build roles required")
    for key in deployment.children:
        build_roles[key] = "api"
    write_private(
        root / "prepared.json",
        {
            "originals": originals,
            "processes": processes,
            "storage": storage,
            "build_roles": build_roles,
        },
        budget=evidence_owner.budget,
    )
    ledger.append(
        "writers-captured",
        {
            "digest": digest(writers.writer_ids),
            "seed": identity,
            "containers_digest": digest(originals),
            "guest_ns": time.monotonic_ns(),
        },
    )
    settings = DeploymentSettings(_env_file=None, **config["settings"])
    if settings.model_dump(mode="json") != config["settings"]:
        raise ValueError("complete provisioned observer settings required")
    from sqlalchemy.engine import make_url

    pg = rows[config["services"]["postgres"]]
    pg_ips = {n["IPAddress"] for n in pg["NetworkSettings"]["Networks"].values()}
    if (
        settings.postgres_host not in pg_ips
        or settings.postgres_db != config["database_name"]
        or settings.postgres_db != binding["database_name"]
    ):
        raise ValueError("observer PostgreSQL is not the exact owned persistence service")
    effective = make_url(str(settings.sqlalchemy_database_uri))
    if (
        effective.host not in pg_ips
        or effective.database != binding["database_name"]
        or effective.port not in {None, 5432}
        or effective.query
    ):
        raise ValueError("effective observer database URL is external or overrides transport")
    if config["services"]["minio"] != binding["minio_container"]:
        raise ValueError("observer MinIO role differs from original service binding")
    if process_snapshot(handoff["parent"]["pid"]) != handoff["parent"]:
        raise ValueError("original seed parent process changed")
    parent_fd = os.pidfd_open(handoff["parent"]["pid"])
    if process_snapshot(handoff["parent"]["pid"]) != handoff["parent"]:
        os.close(parent_fd)
        raise ValueError("seed parent changed during capture")
    result, resources, failure = None, None, None
    try:
        async with open_observers(
            settings, binding, owned_containers=rows, evidence_owner=evidence_owner
        ) as resources:
            services = await version_services(resources, binding)

            class ReadFinal:
                async def read(self):
                    # Open original readonly snapshots only AFTER actual exit.
                    with ExitStack() as stack:
                        journals = [
                            stack.enter_context(
                                ReadOnlyRecoveryJournal(
                                    Path(p), budget=evidence_owner.budget.child()
                                )
                            )
                            for p in config["journal_roots"]
                        ]
                        output = stack.enter_context(RecoveryJournal(root))

                        async def fence():
                            for key, original in originals.items():
                                from scripts.execution_capacity.guest_seal import inspect_container

                                verify_incarnation(
                                    inspect_container(key),
                                    original,
                                    exited=key not in config["services"].values(),
                                )

                        final = FinalInventory.from_resources(
                            writers=writers,
                            resources=resources,
                            authorization=AuthorizationContext.system("execution-kernel"),
                            journals=journals,
                            output_journal=output,
                            binding=binding,
                            seed=config["seed"],
                            origin=SourceOrigin(
                                kind="base",
                                seal_id=config["seal_id"],
                                round=None,
                                boot_id=None,
                                clone_id=None,
                            ),
                            services=services,
                            signing_secret=settings.database_authorization_signing_secret,
                            source_root=Path(config["source_root"]),
                            build_groups=config["build_groups"],
                            host_fence=fence,
                            docker=docker,
                        )
                        return await final.read()

            result = await quiesce(
                writers=writers,
                workloads=[
                    SeedDrain(
                        seed_root,
                        ready,
                        child,
                        originals[child],
                        parent_fd,
                        budget=evidence_owner.budget,
                    )
                ],
                diagnostics=[],
                uploads=[],
                final=ReadFinal(),
            )
    except BaseException as error:  # noqa: BLE001 - retain primary and independent writer cleanup errors
        failure = error
        if result is None:
            try:
                exits = await writers.stop()
                ledger.append("failed-cleanup-writer-exits", {"exits": exits})
            except BaseException as cleanup_error:  # noqa: BLE001 - never replace primary failure
                failure = BaseExceptionGroup(
                    "base observer and writer cleanup failures", [error, cleanup_error]
                )
    os.close(parent_fd)
    evidence = {
        "quiescence": result,
        "observer_closed_ns": None if resources is None else resources.closed_ns,
        "errors": [] if failure is None else [{"type": type(failure).__name__}],
    }
    try:
        evidence_owner.retain_cleanup_prefix(root, evidence)
    except BaseException as retention_error:
        primary = failure if failure is not None else retention_error
        evidence_owner.retain_failure(root, primary, resources=resources)
        if failure is not None:
            raise BaseExceptionGroup(
                "original acquisition and retention failures", [failure, retention_error]
            ) from None
        raise
    if failure is not None:
        evidence_owner.retain_failure(root, failure, resources=resources)
        raise failure
    result.require_complete()
    if resources.closed_ns is None:
        raise ValueError("actual readonly observer closure missing")
    source = result.final["source"].require_complete()
    if source.database["database_system_identifier"] != storage["pg"]["system_identifier"]:
        raise ValueError("inventory and physical PG cluster differ")
    payload = export_writer_base(
        result, config["seal_id"], owner=evidence_owner.journal, budget=evidence_owner.budget
    )
    from scripts.execution_capacity.evidence_json import json_digest

    complete_digest = json_digest(
        result, budget=evidence_owner.budget, owner=evidence_owner.journal
    )
    payload["complete_quiescence_digest"] = complete_digest
    projectors = {
        (str(p["source_version"]), str(p["algorithm_version"]), str(p["generation"]))
        for p in source.projectors
    }
    batches = {str(a["batch_id"]) for a in source.attempts}
    if len(projectors) != 1 or len(batches) != 1:
        raise ValueError("unique actual base projector and completed corpus batch required")
    projector = next(iter(projectors))
    metadata = {
        "seal_id": config["seal_id"],
        "fixture_id": binding["fixture_id"],
        "fixture_manifest_digest": digest(
            read_private(seed_root / "fixture.json", budget=evidence_owner.budget)
        ),
        "source_digest": source.build["source_digest"],
        "configuration_digest": digest(binding),
        "policy_id": binding["policy_revision"],
        "migration": source.database["migrations"][0],
        "projector_source_version": projector[0],
        "read_algorithm_version": projector[1],
        "generation": projector[2],
        "metric_version": source.build["metric_version"],
        "completed_corpus_batch_id": next(iter(batches)),
    }
    cleanup = {
        **evidence,
        "seal_metadata": metadata,
        "writer_base": payload,
        "writer_quiescence_digest": digest(payload),
        "complete_quiescence_digest": complete_digest,
        "source_inventory": source,
        "source_safe": source.safe(owner=evidence_owner.journal, budget=evidence_owner.budget),
        "source_inventory_digest": source.safe_digest(
            owner=evidence_owner.journal, budget=evidence_owner.budget
        ),
        "storage": storage,
    }

    from scripts.execution_capacity.c2c_export import close_originals

    try:
        final = close_originals(
            root,
            config,
            ledger,
            {
                "cleanup": cleanup,
                "operands": evidence_owner.originals,
                "objects": resources.evidence_objects.originals,
                "transports": resources.evidence_transport.originals,
                "sql": evidence_owner.sql_reads,
            },
            budget=evidence_owner.budget,
        )
    except BaseException as error:
        evidence_owner.retain_failure(root, error, resources=resources)
        raise
    return {**cleanup, "c2c_final": final}
