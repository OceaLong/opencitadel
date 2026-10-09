"""Immutable work-unit anchors and explicit infrastructure replacement ancestry."""

from sqlalchemy import text

from app.domain.evaluation.configuration import digest
from app.domain.execution.run import RunState
from app.domain.models.scope import Principal
from app.infrastructure.repositories.db_evaluation_budget_control_repository import (
    DBEvaluationBudgetControlRepository,
)
from app.infrastructure.repositories.db_evaluation_dataset_repository import (
    DBEvaluationDatasetRepository,
)
from app.infrastructure.repositories.db_evaluation_recording_repository import (
    DBEvaluationRecordingRepository,
)


def work_unit(binding):
    return digest(
        {
            key: str(getattr(binding, key))
            for key in (
                "namespace_id",
                "case_id",
                "subject_config_version_id",
                "config_version_id",
                "repeat",
                "purpose",
            )
        }
    )


class DBEvaluationLineageRepository:
    def __init__(self, session):
        self.db = session
        self.controls = DBEvaluationBudgetControlRepository(session)

    async def get(self, scope, run_id):
        binding = await self.controls.binding(scope, run_id)
        if binding is None:
            raise ValueError("budget_run_binding_unavailable")
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM evaluation_run_lineages WHERE run_id=:run AND work_unit_id=:unit"
                    ),
                    {"run": run_id, "unit": work_unit(binding)},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def assert_current(self, scope, run_id):
        # Callers hold the namespace lock, which also serializes replacement links.
        lineage = await self.get(scope, run_id)
        if lineage is None:
            raise ValueError("budget_logical_lineage_unavailable")
        child = await self.db.scalar(
            text("SELECT run_id FROM evaluation_run_lineages WHERE predecessor_run_id=:run"),
            {"run": run_id},
        )
        if child is not None:
            raise ValueError("budget_lineage_superseded")
        return lineage

    async def _binding(self, scope, run_id):
        binding = await self.controls.binding(scope, run_id)
        if binding is None:
            raise ValueError("budget_run_binding_unavailable")
        namespace = await self.controls.namespace(scope, binding.namespace_id, lock=True)
        if namespace.state != "open" or namespace.requester != binding.requester:
            raise ValueError("budget_lineage_namespace_unavailable")
        await DBEvaluationDatasetRepository(self.db).authorize(
            scope, Principal.model_validate(binding.requester), write=True
        )
        return binding

    async def ensure_initial(self, scope, run_id):
        binding = await self._binding(scope, run_id)
        existing = await self.get(scope, run_id)
        if existing is not None:
            return existing
        unit = work_unit(binding)
        anchor = await self.db.scalar(
            text("SELECT root_run_id FROM evaluation_work_unit_lineages WHERE id=:id"), {"id": unit}
        )
        if anchor is not None:
            raise ValueError("budget_lineage_predecessor_required")
        await DBEvaluationRecordingRepository(self.db).require_unadmitted(scope, run_id)
        await self.db.execute(
            text(
                "INSERT INTO evaluation_work_unit_lineages(id,namespace_id,root_run_id) VALUES(:id,:namespace,:run)"
            ),
            {"id": unit, "namespace": binding.namespace_id, "run": run_id},
        )
        await self.db.execute(
            text(
                "INSERT INTO evaluation_run_lineages(run_id,work_unit_id,root_run_id) VALUES(:run,:unit,:run)"
            ),
            {"run": run_id, "unit": unit},
        )
        return await self.get(scope, run_id)

    async def link_replacement(self, scope, run_id, *, predecessor_run_id, expected_generation):
        if type(expected_generation) is not int or expected_generation < 1:
            raise ValueError("budget_lineage_generation_invalid")
        binding = await self._binding(scope, run_id)
        prior = await self.controls.binding(scope, predecessor_run_id)
        if (
            prior is None
            or prior.run_id == run_id
            or work_unit(prior) != work_unit(binding)
            or prior.requester != binding.requester
            or prior.policy_digest != binding.policy_digest
            or prior.config_fingerprint != binding.config_fingerprint
        ):
            raise ValueError("budget_lineage_predecessor_mismatch")
        unit = work_unit(binding)
        root = await self.db.scalar(
            text("SELECT root_run_id FROM evaluation_work_unit_lineages WHERE id=:id"), {"id": unit}
        )
        previous = await self.get(scope, predecessor_run_id)
        if root is None or previous is None or previous["root_run_id"] != root:
            raise ValueError("budget_lineage_predecessor_required")
        existing = await self.get(scope, run_id)
        if existing is not None:
            if (
                existing["predecessor_run_id"] != predecessor_run_id
                or existing["predecessor_generation"] != expected_generation
            ):
                raise ValueError("budget_lineage_conflict")
            return existing
        await DBEvaluationRecordingRepository(self.db).require_unadmitted(scope, run_id)
        lease = (
            (
                await self.db.execute(
                    text("SELECT * FROM evaluation_execution_leases WHERE run_id=:run FOR UPDATE"),
                    {"run": predecessor_run_id},
                )
            )
            .mappings()
            .first()
        )
        if lease is None or lease["generation"] != expected_generation:
            raise ValueError("budget_lineage_generation_stale")
        state = RunState.model_validate(lease["state"]) if lease["state"] else None
        if (
            lease["phase"] != "released"
            or state is None
            or not (
                state.status == "failed"
                or (state.status == "waiting" and state.wait_reason == "retry")
            )
            or any(
                status == "unknown"
                for _, status, generation in state.settled_activities
                if generation == state.retry_generation
            )
        ):
            raise ValueError("budget_lineage_predecessor_state")
        unknown = await self.db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM execution_model_dispatches d JOIN evaluation_run_lineages l ON l.run_id=d.run_id WHERE l.root_run_id=:root AND NOT EXISTS(SELECT 1 FROM execution_model_settlements s WHERE s.scope_key=d.scope_key AND s.call_identity=d.call_identity))"
            ),
            {"root": root},
        )
        if unknown:
            raise ValueError("budget_lineage_unknown_effect")
        child = await self.db.scalar(
            text("SELECT run_id FROM evaluation_run_lineages WHERE predecessor_run_id=:prior"),
            {"prior": predecessor_run_id},
        )
        if child is not None:
            raise ValueError("budget_lineage_conflict")
        await self.db.execute(
            text(
                "INSERT INTO evaluation_run_lineages(run_id,work_unit_id,root_run_id,predecessor_run_id,predecessor_generation) VALUES(:run,:unit,:root,:prior,:generation)"
            ),
            {
                "run": run_id,
                "unit": unit,
                "root": root,
                "prior": predecessor_run_id,
                "generation": expected_generation,
            },
        )
        return await self.get(scope, run_id)
