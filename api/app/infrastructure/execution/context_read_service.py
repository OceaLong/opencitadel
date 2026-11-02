"""Bind an internal read_source call to the model that formally requested it."""

from sqlalchemy import text

from app.application.execution import activity_types
from app.domain.execution.context_models import ContextCompilation
from app.domain.execution.tool_contract import ToolApprovalSubject
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader


class ContextReadService:
    def __init__(self, *, uow_factory, objects, pages):
        self.uow_factory, self.objects, self.pages = uow_factory, objects, pages

    async def read_for_activity(self, context, *, source_id, cursor=None, limit=8192):
        scope = context.run.owner_scope
        async with self.uow_factory(AuthorizationContext.system("execution-kernel")) as work:
            state = (
                await PostgresVerifiedRunLoader(work.db_session).load(
                    context.run.run_id,
                    owner_scope=(None if scope.team_id else scope.user_id, scope.team_id),
                    required_pin=context.run.definition_pin,
                )
            ).replay.state
            if (
                context.activity_id,
                activity_types.TOOL_CALL,
                context.generation,
            ) not in state.requested_activities:
                raise ValueError("CONTEXT_SOURCE_READ_LINEAGE_INVALID")

            async def request(identity):
                return await work.db_session.scalar(
                    text(
                        "SELECT request_payload FROM execution_activity_tasks WHERE activity_id=:activity "
                        "AND aggregate_id=CAST(CAST(:run AS uuid) AS text) AND request_generation=:generation"
                    ),
                    {"activity": identity, "run": state.run_id, "generation": context.generation},
                )

            body = await request(context.activity_id)
            if not isinstance(body, dict) or not isinstance(body.get("subject"), dict):
                raise ValueError("CONTEXT_SOURCE_READ_LINEAGE_INVALID")  # noqa: TRY004 - stable failure code
            subject = ToolApprovalSubject.model_validate(body["subject"])
            if (
                subject.tool_name != "context_read_source"
                or body.get("tool_call", {}).get("call_id") != subject.tool_call_id
            ):
                raise ValueError("CONTEXT_SOURCE_READ_LINEAGE_INVALID")
            model_id = subject.originating_activity_id
            if (
                model_id,
                activity_types.MODEL_CALL,
                context.generation,
            ) not in state.requested_activities or (
                model_id,
                "succeeded",
                context.generation,
            ) not in state.settled_activities:
                raise ValueError("CONTEXT_SOURCE_READ_LINEAGE_INVALID")
            model_request = await request(model_id)
            ref = model_request.get("context_ref") if isinstance(model_request, dict) else None
            if not isinstance(ref, str) or not any(
                result == ref
                and generation == context.generation
                and (identity, activity_types.CONTEXT_PREPARE, generation)
                in state.requested_activities
                and (identity, "succeeded", generation) in state.settled_activities
                for identity, generation, result, _, _ in state.activity_results
            ):
                raise ValueError("CONTEXT_SOURCE_READ_LINEAGE_INVALID")
            prepared = await self.objects.load_context_document(ref)
            manifest = ContextCompilation.model_validate(prepared.get("compilation")).manifest
            if (
                prepared.get("kind") != "context_prepare"
                or manifest is None
                or manifest.definition_pin != state.definition_pin
                or manifest.boundary.current_run_id != state.run_id
                or manifest.digest != model_request.get("context_digest")
                or not any(
                    tool.get("function", {}).get("name") == subject.tool_name
                    for tool in manifest.tools
                )
            ):
                raise ValueError("CONTEXT_SOURCE_READ_LINEAGE_INVALID")
        return await self.pages.read(manifest, source_id=source_id, cursor=cursor, limit=limit)
