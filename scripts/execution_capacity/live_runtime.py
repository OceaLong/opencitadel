"""Actual finite live admissions alongside independently running full kernels.

C owns native observers, startup/reset and host containment. This controller
never creates a handler outcome, calls a progress sink, or warms a target query.
Its only queries address the fresh live Runs and separate batch, not corpus hot
Runs/analysis/history. Every window is single-use, including failed attempts.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from scripts.execution_capacity.batch import Operations, start_batch
from scripts.execution_capacity.live import progress_records, validate_topology, validate_updates
from scripts.execution_capacity.live_facts import LiveFacts, validate_active
from scripts.execution_capacity.runtime import KERNEL, verify_prerequisite
from sqlalchemy import text

from app.application.security.authorization_context import authorization_scope
from app.composition.evaluation import build_batch_scheduler
from app.composition.execution_content import build_execution_view_service
from app.domain.evaluation.batch import TERMINAL_BATCH
from app.domain.evaluation.configuration import DeploymentLimits
from app.domain.models.scope import OwnerScope


def session_binding(base, prerequisite):
    if set(prerequisite) != {"session_id", "session_created_at", "model_id", "endpoint_id"}:
        raise ValueError("session prerequisite cannot override deployment authority")
    return {**base, **prerequisite}


def window_plan(plan, minimal_ready_ns):
    startup, seconds, sessions = plan["startup_seconds"], plan["seconds"], plan["sessions"]
    if (
        type(startup) is not int
        or not 3 <= startup <= 20
        or type(seconds) is not int
        or not 2 <= seconds <= 30
        or len(sessions) != 10
        or len({s["session_id"] for s in sessions}) != 10
    ):
        raise ValueError("invalid preregistered finite live window")
    start = minimal_ready_ns + startup * 1_000_000_000
    return start, start + seconds * 1_000_000_000


def validate_batch_window(before, after, defaults):
    for row in (before, after):
        if row["status"] != "running" or any(
            row["settings"].get(k) != v for k, v in defaults.items()
        ):
            raise ValueError("live batch completed/stopped or default limits changed")
    if after["sends"] <= before["sends"] or after["settled"] <= before["settled"]:
        raise ValueError("live batch did not actually dispatch and settle in window")


class LiveWorkload:
    def __init__(self, resources, shared, journal, binding, *, bridge=None):
        self.resources, self.shared, self.journal, self.binding = (
            resources,
            shared,
            journal,
            binding,
        )
        self.bridge = bridge
        self.settings = resources.settings
        self.live = binding["live"]
        self.topology = validate_topology(self.settings, self.live["workers"])
        self.boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.scope = OwnerScope.personal(binding["principal_id"])
        self.facts = LiveFacts(
            resources.postgres.session_factory, KERNEL, journal, self.scope, None
        )
        self.tasks = []
        self.sessions = []
        self.runs = []
        self.batch_id = None
        self.authorized = None
        self.principal = None
        scheduler = build_batch_scheduler(
            settings=self.settings, resources=resources, shared=shared
        )
        from app.application.evaluation.batch_service import BatchService

        self.suites = scheduler.suites
        self.batches = BatchService(scheduler.suites, preflight_factory=scheduler.preflight_factory)

    async def verify_policies(self):
        from app.composition.evaluation_execution import configured_execution_policy
        from app.composition.physical_budget import configured_physical_policy
        from app.infrastructure.repositories.db_evaluation_budget_policy_repository import (
            DBEvaluationBudgetPolicyRepository,
        )
        from app.infrastructure.repositories.db_evaluation_execution_repository import (
            DBEvaluationExecutionRepository,
        )

        async with self.facts.session() as session:
            actual = await DBEvaluationBudgetPolicyRepository(session).active()
            execution = await DBEvaluationExecutionRepository(session).active_policy()
            if actual != configured_physical_policy(
                self.settings
            ) or execution != configured_execution_policy(self.settings):
                raise ValueError("actual policy differs; live controller never activates limits")
            # Reject occupied/unknown physical buckets before new cohort. This is
            # deliberately conservative for the exclusively owned environment.
            if await session.scalar(
                text("SELECT count(*) FROM evaluation_budget_buckets WHERE slots<>0")
            ):
                raise ValueError("preexisting physical occupancy/unknown must be reconciled")
        workers = [
            r["body"] for _, r in self.journal.records("live_worker") if r["receipt"] is None
        ]
        if (
            len(workers) != self.live["workers"]
            or {w["hostname"] for w in workers} != set(self.live["worker_hostnames"])
            or any(
                w["source_sha256"] != self.binding["source_sha256"]
                or w["boot_id"] != self.boot
                or w["topology"] != self.topology
                for w in workers
            )
        ):
            raise ValueError("actual full kernel process/profile/boot inventory differs")

    async def admit(self, prerequisite, identity, *, profile):
        local = session_binding(self.binding, prerequisite)
        authorized, policy = await verify_prerequisite(self.shared, self.facts, local)
        if (
            policy.common.activity.tool_timeout_seconds < 70
            or policy.common.model_resilience.max_call_budget_seconds < 70
        ):
            raise ValueError("current real timeout policy cannot contain finite live response")
        with authorization_scope(authorized):
            model = await self.shared.inference_model_service.resolve_chat(
                local["model_id"], scope=self.scope
            )
        if (
            model.model_name != profile
            or model.base_url != self.binding["provider_endpoint"]
            or model.provider.value != "openai"
            or not 600 <= model.model.settings.max_output_tokens <= 4096
            or float(model.extra_params.get("request_timeout", 300)) < 70
        ):
            raise ValueError("actual admission model/profile/output/deadline differs")
        if self.authorized is not None and self.authorized != authorized:
            raise ValueError("live principal authority changed")
        self.authorized, self.principal = authorized, authorized.principal
        session_id = local["session_id"]
        before = time.monotonic_ns()
        self.journal.intent(
            "live_admission",
            identity,
            {
                "session_id": session_id,
                "scope": self.facts.scope_key,
                "model_id": model.id,
                "profile": profile,
                "before_ns": before,
                "boot_id": self.boot,
            },
        )
        self.sessions.append(session_id)

        async def consume():
            with authorization_scope(authorized):
                async for event in self.shared.agent_service.chat(
                    session_id,
                    owner_scope=self.scope,
                    message="Capacity ordinary Ask",
                    request_id=identity,
                ):
                    if event.event_type == "resource_build":
                        # Public feed receipt is not DOM paint and has its own time.
                        self.journal.intent(
                            "live_public",
                            str(uuid4()),
                            {
                                "session_id": session_id,
                                "event_id": str(event.payload.get("event_id")),
                                "message": event.payload.get("message"),
                                "received_ns": time.monotonic_ns(),
                                "boot_id": self.boot,
                            },
                        )

        task = asyncio.create_task(consume())
        self.tasks.append(task)
        while True:
            if task.done():
                await task
            async with self.shared.uow_factory(authorized) as work:
                current = await work.session.get_by_id(session_id, scope=self.scope)
                if (
                    current is not None
                    and current.active_execution_request_id == identity
                    and current.active_execution_run_id is not None
                ):
                    run = str(current.active_execution_run_id)
                    after = time.monotonic_ns()
                    self.journal.acknowledge(
                        "live_admission", identity, {"run_id": run, "after_ns": after}
                    )
                    self.runs.append(run)
                    return {
                        "run_id": run,
                        "before_ns": before,
                        "after_ns": after,
                        "session_id": session_id,
                    }
            await asyncio.sleep(0.02)

    async def start_batch(self, window_id):
        with authorization_scope(self.authorized):
            suite = await self.suites.get_version(
                self.scope, self.principal, "suite", UUID(self.live["suite_version"])
            )
            defaults = {
                k: getattr(self.settings, "evaluation_" + k) for k in DeploymentLimits.model_fields
            }
            if (
                suite.quantity != 5000
                or suite.settings.repeat != 1
                or any(getattr(suite.settings, k) != v for k, v in defaults.items())
            ):
                raise ValueError(
                    "live batch must retain actual 5000-result default-concurrency suite"
                )
            # Read current subject and judge bindings through normal authorization.
            rubric = await self.suites.get_version(
                self.scope, self.principal, "rubric", suite.rubric_version
            )
            for cid in [*suite.config_versions, rubric.judge_config_version]:
                config = await self.suites.get_version(self.scope, self.principal, "config", cid)
                model = await self.shared.inference_model_service.resolve_chat(
                    config.selection.model_id, scope=self.scope
                )
                if (
                    model.model_name != "acceptance-capacity"
                    or model.base_url != self.binding["provider_endpoint"]
                ):
                    raise ValueError("live batch must use exact fixed100ms owned provider")
            operations = Operations(
                self.journal, window_id, self.facts.scope_key, self.principal.user_id
            )
            result = await start_batch(self.batches, self.scope, self.principal, operations, suite)
            if str(result.id) == self.live["completed_corpus_batch_id"]:
                raise ValueError("completed standard corpus is not live load")
            self.batch_id = result.id
            self.defaults = defaults
            self.journal.intent(
                "live_batch",
                result.id,
                {
                    "window_id": str(window_id),
                    "suite_version": str(suite.id),
                    "defaults": defaults,
                    "batch_results": suite.quantity,
                },
            )

    def progress_rows(self):
        values = []
        for record in progress_records(self.journal, self.runs):
            body, receipt = record["body"], record["receipt"]
            if body["run_id"] in self.runs and body.get("phase") == "model_response":
                if body["boot_id"] != self.boot:
                    raise ValueError("incomparable worker clock epoch")
                missing = (
                    {"ack": False, "after_ns": None, "error": "missing-persistence-receipt"}
                    if self.bridge is not None
                    else {"ack": False, "after_ns": time.monotonic_ns()}
                )
                values.append({**body, **(receipt or missing)})
        return values

    async def publish_incremental(self):
        from scripts.execution_capacity.source_export import publish_progress

        if self.bridge is None:
            return
        if not hasattr(self, "published_progress"):
            self.published_progress = set()
        # A pending receipt is not an acknowledgement. It remains in the source
        # journal; final window validation still requires all original updates.
        rows = [
            r
            for r in self.progress_rows()
            if r["ack"] is True
            and r.get("error") is None
            and r["event_id"] not in self.published_progress
        ]
        for offset in range(0, len(rows), 16):
            before = time.monotonic_ns()
            proved = await self.facts.progress(rows[offset : offset + 16])
            after = time.monotonic_ns()
            # An observation projection may lag the sink commit. Only retry its
            # readback, never the underlying operation; missing final joins fail.
            joined = [r for r in proved if r["effective"] and r["public"]]
            publish_progress(
                self.bridge,
                joined,
                self.measurement,
                start_ns=self.source_start_ns,
                query_before_ns=before,
                query_after_ns=after,
            )
            self.published_progress.update(r["event_id"] for r in joined)

    def close_measurement(self):
        # Pending writes begun before the cutoff may still commit in the tail.
        # Their final disposition and real SQL/public publication must settle
        # before a stable measured-feed cursor can authorize client completion.
        rows = [
            r
            for r in self.progress_rows()
            if r["before_ns"] < self.source_start_ns + self.measurement.end_offset_ns
        ]
        if any(r.get("error") not in (None, "missing-persistence-receipt") for r in rows):
            raise ValueError("measured persistence failure retained")
        if any(r["ack"] is not True for r in rows):
            return None
        return self.bridge.close_measurement([r["event_id"] for r in rows])

    async def snapshot(self, *, counts=False):
        before_ns = time.monotonic_ns()
        active = await self.facts.active(self.runs)
        after_ns = time.monotonic_ns()
        if self.bridge is not None:
            self.last_snapshot_id = str(uuid4())
            self.journal.intent(
                "live_claim_snapshot",
                self.last_snapshot_id,
                {
                    "window_id": self.bridge.key,
                    "before_ns": before_ns,
                    "after_ns": after_ns,
                    "boot_id": self.boot,
                    "rows": [
                        {
                            k: (
                                v.isoformat()
                                if hasattr(v, "isoformat")
                                else str(v)
                                if isinstance(v, UUID)
                                else v
                            )
                            for k, v in row.items()
                        }
                        for row in active
                    ],
                },
            )
        claims = validate_active(active, self.runs)
        before_ns = time.monotonic_ns()
        batch = await self.facts.batch(self.batch_id, counts=counts or self.bridge is not None)
        after_ns = time.monotonic_ns()
        if self.bridge is not None:
            self.journal.intent(
                "live_batch_snapshot",
                self.last_snapshot_id,
                {
                    "window_id": self.bridge.key,
                    "boot_id": self.boot,
                    "before_ns": before_ns,
                    "after_ns": after_ns,
                    "batch_id": str(self.batch_id),
                    "suite_version": str(batch["suite_version"]),
                    "batch": {**batch, "suite_version": str(batch["suite_version"])},
                },
            )
        if batch["status"] != "running" or any(
            batch["settings"].get(k) != v for k, v in self.defaults.items()
        ):
            raise ValueError("actual background batch is not running at defaults")
        acknowledged = {
            r["run_id"] for r in self.progress_rows() if r["ack"] is True and r["progress"] == 0
        }
        if acknowledged != set(self.runs):
            raise ValueError("live handlers lack real received-fragment acknowledgement")
        return claims, batch

    async def finish(self):
        """Let finite ordinary calls end; cancel batch lawfully; retain unknowns."""
        from scripts.execution_capacity.batch_facts import BatchFacts

        if self.batch_id is not None:
            with authorization_scope(self.authorized):
                request = str(uuid5(self.batch_id, "live:cancel"))
                self.journal.intent("live_cancel", self.batch_id, {"request_id": request})
                await self.batches.cancel(
                    self.scope, self.principal, request, {"batch_id": str(self.batch_id)}
                )
            facts = BatchFacts(
                self.facts.sessions, KERNEL, self.journal, self.scope, None, batch_id=self.batch_id
            )
        deadline = time.monotonic() + self.settings.shutdown_timeout_seconds + 120
        try:
            while time.monotonic() < deadline:
                dispositions = await self.facts.disposition(self.runs)
                pending = any(not t.done() for t in self.tasks)
                if self.batch_id is not None:
                    with authorization_scope(self.authorized):
                        batch = await self.batches.get(self.scope, self.principal, self.batch_id)
                    _, clean = await facts.environments(final=True)
                    pending |= (
                        batch.status not in TERMINAL_BATCH
                        or batch.cleanup_status != "clean"
                        or not clean
                    )
                outcomes = await self.facts.outcomes(self.runs)
                pending |= set(outcomes) != set(self.runs) or any(
                    s not in {"completed", "failed", "cancelled"} for s in outcomes.values()
                )
                if not pending:
                    await asyncio.gather(*self.tasks)
                    if len(self.sessions) != len(self.runs):
                        raise ValueError(
                            "response-lost admission requires exact session/request recovery"
                        )
                    if len({r["run_id"] for r in dispositions}) != len(self.runs) or any(
                        r["state"] != "settled" or r["fact"] is None for r in dispositions
                    ):
                        raise ValueError("live physical dispatch remains unknown/unmeasured")
                    self.journal.intent(
                        "live_disposition",
                        str(uuid4()),
                        {
                            "runs": self.runs,
                            "dispatches": dispositions,
                            "immutable_history": "retained",
                            "outcomes": outcomes,
                            "host_physical_cleanup": "pending_C",
                        },
                    )
                    if any(s != "completed" for s in outcomes.values()):
                        raise ValueError("finite ordinary live Run did not complete successfully")
                    return
                await asyncio.sleep(0.25)
            raise TimeoutError(
                "live lifecycle did not converge; retained unknowns require recovery"
            )
        finally:
            # Stop local subscribers only. Never fabricate cancellation of the
            # actual workers/physical sends or delete their retained evidence.
            for task in self.tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)

    @asynccontextmanager
    async def window(self, window_id, *, minimal_ready_ns):
        plan = self.live["windows"][str(window_id)]
        start, end = window_plan(plan, minimal_ready_ns)
        if self.bridge is not None:
            from scripts.acceptance.capacity_models import MeasurementInterval

            self.measurement = MeasurementInterval.model_validate(plan["measurement"])
            if (
                self.measurement.end_offset_ns + 2_000_000_000 + self.measurement.control_margin_ns
                > end - start
            ):
                raise ValueError("measurement guard does not fit immutable source window")
            self.source_start_ns = start
            self.bridge.publish(
                "measurement",
                {"rule": self.measurement.model_dump(), "start_ns": start, "end_ns": end},
            )
        if self.journal.get("live_window", window_id) is not None:
            raise ValueError("live window already attempted; no replacement samples")
        self.journal.intent(
            "live_window",
            window_id,
            {
                "plan": plan,
                "start_ns": start,
                "end_ns": end,
                "boot_id": self.boot,
                "source_sha256": self.binding["source_sha256"],
            },
        )
        error = None
        monitor = None
        try:
            await self.verify_policies()
            # Each admission has its own timeout within the immutable deadline.
            async with asyncio.timeout(max(0, (start - time.monotonic_ns()) / 1e9 - 2)):
                for index, session in enumerate(plan["sessions"]):
                    await self.admit(
                        session, uuid5(window_id, f"live:{index}"), profile="acceptance-live"
                    )
                await self.start_batch(window_id)
                while True:
                    try:
                        initial, _ = await self.snapshot()
                        break
                    except ValueError:
                        await asyncio.sleep(0.05)
            if self.bridge is not None:
                self.bridge.establish(
                    sessions=dict(zip(self.sessions, self.runs, strict=True)),
                    claims=initial,
                    batch_id=str(self.batch_id),
                    start_ns=start,
                    end_ns=end,
                    snapshot_id=self.last_snapshot_id,
                )
            # Bridge snapshots include query brackets; leave headroom within the
            # fixed 100 ms continuity requirement. Standalone B3b stays unchanged.
            poll_delay = 0.05 if self.bridge is not None else 0.1
            # Required continuous load for final two setup seconds.
            while time.monotonic_ns() < start:
                claims, _ = await self.snapshot()
                if claims != initial:
                    raise ValueError("live cohort changed before fixed deadline")
                if self.bridge is not None:
                    await self.publish_incremental()
                await asyncio.sleep(min(poll_delay, max(0, (start - time.monotonic_ns()) / 1e9)))
            if self.bridge is not None:
                self.bridge.require_ready()
            _, before = await self.snapshot(counts=True)

            async def watch():
                while time.monotonic_ns() < end:
                    claims, _ = await self.snapshot()
                    if claims != initial:
                        raise ValueError("live cohort lost/replaced during window")
                    if self.bridge is not None:
                        await self.publish_incremental()
                        self.close_measurement()
                    await asyncio.sleep(min(poll_delay, max(0, (end - time.monotonic_ns()) / 1e9)))
                claims, after = await self.snapshot(counts=True)
                if claims != initial:
                    raise ValueError("live handler ended inside window")
                if self.bridge is not None:
                    await self.publish_incremental()
                validate_batch_window(before, after, self.defaults)
                return after

            monitor = asyncio.create_task(watch())
            yield {
                "window_id": str(window_id),
                "run_ids": list(self.runs),
                "batch_id": str(self.batch_id),
                "start_ns": start,
                "end_ns": end,
                "boot_id": self.boot,
                "source_only": self.bridge is None,
            }
            if self.bridge is not None:
                self.bridge.require_done()
            elif time.monotonic_ns() > end:
                raise ValueError("target operation exceeded preregistered load window")
            after = await monitor
            rows = self.progress_rows()
            # Keep every failed in-window submission and every in-window commit;
            # never count final100 phase markers as continuing load.
            selected = [
                r
                for r in rows
                if (r["after_ns"] is not None and start <= r["after_ns"] < end)
                or (r["ack"] is not True and start <= r["before_ns"] < end)
            ]
            proved = await self.facts.progress(selected)
            rates = validate_updates(proved, self.runs, start, end)
            views = build_execution_view_service(
                settings=self.settings, resources=self.resources, authorization=self.authorized
            )
            from app.application.execution.view_facts import attempt_key

            public = []
            for run in self.runs:
                latest = max((r for r in proved if r["run_id"] == run), key=lambda r: r["sequence"])
                step_id = attempt_key(
                    latest["activity_id"], latest["generation"], latest["claim_generation"]
                )
                step = await views.get_step(self.scope, UUID(run), step_id)
                if step.public_summary != latest["message"] and not any(
                    r["run_id"] == run
                    and r["sequence"] >= latest["sequence"]
                    and r["message"] == step.public_summary
                    and r["ack"] is True
                    for r in self.progress_rows()
                ):
                    raise ValueError("public step does not join actual received-fragment fact")
                if step.projection_revision < latest["projection_revision"]:
                    raise ValueError("public step lags committed source revision")
                public.append(
                    {
                        "run_id": run,
                        "step_id": step.step_id,
                        "projection_revision": step.projection_revision,
                        "public_summary": step.public_summary,
                        "read_ns": time.monotonic_ns(),
                    }
                )
            self.journal.acknowledge(
                "live_window",
                window_id,
                {
                    "rates": rates,
                    "updates": proved,
                    "public_steps": public,
                    "batch_before": {k: before[k] for k in ("sends", "settled")},
                    "batch_after": {k: after[k] for k in ("sends", "settled")},
                    "native_paint": "pending_C",
                    "capacity_acceptance": False,
                },
            )
        except BaseException as failure:
            error = failure
            self.journal.intent(
                "live_failure",
                str(uuid4()),
                {"window_id": str(window_id), "type": type(failure).__name__},
            )
            raise
        finally:
            if monitor is not None and not monitor.done():
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            try:
                await self.finish()
            except BaseException as cleanup:
                if error is not None:
                    raise BaseExceptionGroup(
                        "live window and recovery failure", [error, cleanup]
                    ) from None
                raise


async def admission_probe(resources, shared, journal, binding, prerequisite, identity):
    """Baseline and loaded probes use this identical real path/profile.

    C schedules one fresh declared session for each probe. This returns a
    conservative admission-to-persisted-attach duration, including the same
    readback overhead in both cohorts. No background load is changed here.
    """
    workload = LiveWorkload(resources, shared, journal, binding)
    primary = None
    try:
        async with asyncio.timeout(30):
            receipt = await workload.admit(prerequisite, identity, profile="acceptance-capacity")
        return {
            **receipt,
            "milliseconds": (receipt["after_ns"] - receipt["before_ns"]) / 1e6,
            "profile": "acceptance-capacity",
            "clock": "guest-monotonic",
            "boot_id": workload.boot,
        }
    except BaseException as failure:
        primary = failure
        raise
    finally:
        try:
            await workload.finish()
        except BaseException as cleanup:
            if primary is not None:
                raise BaseExceptionGroup(
                    "admission probe and recovery failed", [primary, cleanup]
                ) from None
            raise
