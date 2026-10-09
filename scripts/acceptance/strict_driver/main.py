"""Mounted plain-Python strict producer. Running this performs real owned work.

Import and pytest collection do not open resources. Application imports occur
only after input/environment validation in the explicitly invoked entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# /acceptance-driver contains only test source; /app contains the owned image.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strict_driver.contracts import DriverInput


def emit(kind, body):
    print(json.dumps({"kind": kind, "body": body}, sort_keys=True, default=str), flush=True)


@dataclass
class Context:
    input: DriverInput
    settings: object
    resources: object
    shared: object
    scope: object
    principal: object
    scheduler: object
    allowed: set = field(default_factory=set)
    scenarios: list = field(default_factory=list)
    owned: list = field(default_factory=list)
    batch_id: object = None
    report: dict = field(default_factory=dict)

    def own(self, kind, identity):
        key = (kind, str(identity))
        if key not in self.allowed:
            self.allowed.add(key)
            value = {"kind": kind, "id": str(identity), "scope": self.scope.model_dump(mode="json")}
            self.owned.append(value)
            emit("owned", value)
            if sys.stdin.readline().strip() != "owned-durable":
                raise RuntimeError("host did not durably acknowledge ownership")

    async def register_batch_children(self):
        from sqlalchemy import text
        from strict_driver.ownership import KERNEL

        async with self.shared.uow_factory(KERNEL) as work:
            rows = await work.evaluation_batch.results(self.scope, self.batch_id)
            for row in rows:
                self.own("result", row["id"])
                if row["run_id"]:
                    self.own("run", row["run_id"])
            leases = await work.db_session.scalars(
                text("""
                SELECT id FROM evaluation_environment_leases
                WHERE scope_key=:scope AND case_slot->>'batch_id'=:batch
            """),
                {"scope": "user:" + self.scope.user_id, "batch": str(self.batch_id)},
            )
            for identity in leases:
                self.own("lease", identity)

    async def assert_owned_operation(self, identity):
        from sqlalchemy import text
        from strict_driver.ownership import KERNEL

        async with self.shared.uow_factory(KERNEL) as work:
            lease_id = await work.db_session.scalar(
                text(
                    "SELECT lease_id FROM evaluation_environment_operations WHERE id=:id AND scope_key=:scope"
                ),
                {"id": identity, "scope": "user:" + self.scope.user_id},
            )
        if ("lease", str(lease_id)) not in self.allowed:
            raise RuntimeError("operation does not belong to exact owned lease")
        self.own("operation", identity)


def validate_environment(settings, data):
    if (
        settings.env != "test"
        or not settings.evaluation_acceptance_enabled
        or not settings.evaluation_local_docker_enabled
    ):
        raise RuntimeError("strict driver requires dedicated acceptance test deployment")
    labels = settings.sandbox_labels
    if (
        labels.get("com.opencitadel.acceptance.project") != data.binding.project
        or labels.get("com.opencitadel.acceptance.run") != data.binding.run_id
    ):
        raise RuntimeError("driver deployment identity mismatch")
    for path, expected in (
        (settings.evaluation_test_inventory_path, data.binding.inventory_sha256),
        (settings.evaluation_budget_inventory_path, data.binding.budget_inventory_sha256),
    ):
        if not path or hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise RuntimeError("driver mounted inventory mismatch")


async def produce(data, report):
    from strict_driver.ownership import KERNEL, assert_exclusive
    from strict_driver.recovery import admission_restart, submit_and_fence

    from app.composition.evaluation import build_batch_scheduler, build_environment_registry
    from app.composition.resources import open_process_resources
    from app.composition.shared import build_shared_services
    from app.composition.tasks import TaskSupervisor
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal
    from app.runtime_role import ProcessRole
    from core.config import load_deployment_settings

    settings = load_deployment_settings()
    validate_environment(settings, data)
    report["_stage"] = "resource_startup"
    registry = build_environment_registry(settings)
    target = registry.targets.get(str(data.bootstrap.target.id))
    if target is None or target.revision != data.bootstrap.target.revision or not target.enabled:
        raise RuntimeError("dedicated target identity is not current deployment inventory")
    async with open_process_resources(settings, ProcessRole.EXECUTION_KERNEL) as resources:
        supervisor = TaskSupervisor(shutdown_timeout_seconds=settings.shutdown_timeout_seconds)
        try:
            shared = build_shared_services(resources, supervisor=supervisor)
            await shared.runtime_policy_reader.initialize()
            # The host bridge checks the actual migration head before starting
            # this container; the kernel role deliberately cannot read Alembic's
            # administrative version table.
            report["_stage"] = "operator_lookup"
            async with shared.uow_factory(KERNEL) as work:
                user = await work.user.get_by_id(data.bootstrap.operator_id)
                if user is None or not user.is_active:
                    raise RuntimeError("bootstrap authenticated operator no longer active")
                # Current persisted role/token version; nothing supplied by test
                # input can elevate this principal.
                principal = Principal(
                    user_id=user.id, global_role=user.global_role, token_version=user.token_version
                )
            scope = OwnerScope.model_validate(data.bootstrap.scope)
            report["_stage"] = "bootstrap_authority"
            async with shared.uow_factory(
                AuthorizationContext.for_principal(principal, scope=scope)
            ) as work:
                await work.evaluation_dataset.authorize(scope, principal, write=True)
                if data.bootstrap.session_id == data.bootstrap.analysis_session_id:
                    raise RuntimeError("dated session must be distinct from parallel source")
                for session_id in (data.bootstrap.session_id, data.bootstrap.analysis_session_id):
                    if await work.session.get_by_id(session_id, scope=scope) is None:
                        raise RuntimeError(
                            "bootstrap source session is not currently owned and visible"
                        )
                model = await work.inference_model.get_by_id(data.bootstrap.model_id, scope=scope)
                if model is None or model.endpoint_id != data.bootstrap.endpoint_id:
                    raise RuntimeError("model/endpoint identity mismatch")
                environment = await work.evaluation_environment.registered(
                    scope, "environment", data.bootstrap.environment.id
                )
                if environment.revision != data.bootstrap.environment.revision or [
                    (str(ref.id), ref.revision) for ref in environment.allowed_targets
                ] != [(str(data.bootstrap.target.id), data.bootstrap.target.revision)]:
                    raise RuntimeError("registered environment binding mismatch")
                if {(str(ref.id), ref.revision) for ref in environment.credential_refs} != {
                    (str(ref.id), ref.revision) for ref in data.bootstrap.credentials
                }:
                    raise RuntimeError("registered credentials binding mismatch")
                from app.domain.evaluation.configuration import ConfigVersion, SuiteVersion

                config = ConfigVersion.model_validate(
                    await work.evaluation_configuration.get_version(
                        scope, "config", data.bootstrap.configuration_version.id
                    )
                )
                suite = SuiteVersion.model_validate(
                    await work.evaluation_configuration.get_version(
                        scope, "suite", data.bootstrap.suite_version.id
                    )
                )
                dataset = await work.evaluation_dataset.get_version(
                    scope, data.bootstrap.dataset_version.id
                )
                if (
                    config.entity_id != data.bootstrap.configuration.id
                    or config.revision != data.bootstrap.configuration_version.revision
                    or config.selection.model_id != data.bootstrap.model_id
                    or suite.entity_id != data.bootstrap.suite.id
                    or suite.revision != data.bootstrap.suite_version.revision
                    or suite.dataset_version != data.bootstrap.dataset_version.id
                    or suite.config_versions != (config.id,)
                    or suite.environment_version != environment.id
                    or dataset["dataset_id"] != data.bootstrap.dataset.id
                    or dataset["revision"] != data.bootstrap.dataset_version.revision
                    or data.bootstrap.case_id not in {member["id"] for member in dataset["members"]}
                ):
                    raise RuntimeError("published bootstrap pin mismatch")
            scheduler = build_batch_scheduler(settings=settings, resources=resources, shared=shared)
            report["_stage"] = "scheduler_budget"
            context = Context(
                data, settings, resources, shared, scope, principal, scheduler, report=report
            )
            report["owned"] = context.owned
            report["scenarios"] = context.scenarios
            report["current_authority"] = {
                "user_id": principal.user_id,
                "global_role": principal.global_role,
                "token_version": principal.token_version,
                "scope": scope.model_dump(mode="json"),
            }
            await assert_exclusive(shared.uow_factory, context.allowed)
            from strict_driver.scheduler_budget import scheduler_budget_stop

            await scheduler_budget_stop(context)
            await submit_and_fence(context)
            await admission_restart(context)
            from strict_driver.budget import budget_and_late_cancel
            from strict_driver.environment import reset_and_worker_death

            await budget_and_late_cancel(context)
            await reset_and_worker_death(context)
            from strict_driver.views import dated_analysis_runs, parallel_views

            await parallel_views(context)
            await dated_analysis_runs(context)
        finally:
            await supervisor.stop()


async def run(data):
    from strict_bridge import SCENARIOS

    bootstrap = data.bootstrap.model_dump(mode="json")
    report = {
        "schema_version": 1,
        "binding": data.binding.model_dump(mode="json"),
        "bootstrap_sha256": hashlib.sha256(
            json.dumps(bootstrap, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "status": "failed",
        "scenarios": [],
        "owned": [],
        "errors": [],
    }
    try:
        await produce(data, report)
        actual = {(item["requirement"], item["id"]) for item in report["scenarios"]}
        legacy = {
            ("AC05", "missing_history_available_segment"),
            *(("AC22", name) for name in SCENARIOS["AC22"]),
        }
        missing = (
            {(ac, identity) for ac, identities in SCENARIOS.items() for identity in identities}
            - legacy
            - actual
        )
        if missing:
            report["errors"].append({"type": "missing_scenarios", "identities": sorted(missing)})
        else:
            report["status"] = "passed"
    except Exception as exc:  # noqa: BLE001 - persist sanitized failure identity
        # Exceptions may contain private SQL/physical payload. Retain only a
        # fixed stage, SQLSTATE, and safe assertion identity alongside the type.
        error = {"type": type(exc).__name__, "stage": report.get("_stage", "initialization")}
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if isinstance(sqlstate, str) and len(sqlstate) == 5 and sqlstate.isalnum():
            error["sqlstate"] = sqlstate
        if type(exc) is AssertionError and len(exc.args) == 1:
            identity = exc.args[0]
            if isinstance(identity, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", identity):
                error["assertion_id"] = identity
        report["errors"].append(error)
    report.pop("_stage", None)
    emit("report", report)
    return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    data = DriverInput.model_validate_json(Path(args.input).read_bytes())
    return asyncio.run(run(data))


if __name__ == "__main__":
    raise SystemExit(main())
