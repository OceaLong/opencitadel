"""Recoverable durable review commands. E12 only supervises this real consumer."""

from uuid import NAMESPACE_URL, uuid5

from app.domain.evaluation.batch import ScoringCandidate
from app.domain.evaluation.judge_protocol import RescoreRequest
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal

KERNEL = AuthorizationContext.system("execution-kernel")


class ReviewCommandConsumer:
    def __init__(self, uow_factory, judge):
        self.uow_factory, self.judge = uow_factory, judge

    async def tick(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_review_limit")
        async with self.uow_factory(KERNEL) as work:
            await work.evaluation_review.refresh_commands(limit=limit)
            commands = await work.evaluation_review.claim(limit=limit)
            await work.commit()
        for command in commands:
            await self.consume(command)
        return len(commands)

    async def consume(self, command):
        from app.domain.evaluation.execution_slots import ExecutionCapacityUnavailable

        principal = Principal.model_validate(command["principal"])
        scope = (
            OwnerScope.team(principal.user_id, command["team_id"])
            if command["team_id"]
            else OwnerScope.personal(principal.user_id)
        )
        payload = command["payload"]
        candidate = ScoringCandidate.model_validate(payload["candidate"])
        downstream_request = "review:" + str(command["id"])
        run_id, error = command["judge_run_id"], None
        status = "failed"
        try:
            async with self.uow_factory(KERNEL) as work:
                await work.evaluation_dataset.authorize(scope, principal, write=True)
                await work.evaluation_batch.lock(scope, candidate.batch_id)
                batch = await work.evaluation_batch.get(scope, candidate.batch_id)
                original = Principal.model_validate(batch["principal"])
                original_scope = scope.model_copy(update={"user_id": original.user_id})
                await work.evaluation_dataset.authorize(original_scope, original, write=True)
                identity = uuid5(
                    NAMESPACE_URL,
                    f"judge:{original_scope}:{candidate.result_id}:{downstream_request}",
                )
                prior = (
                    await work.evaluation_judge.get(scope, uuid5(identity, "run"))
                    if command["kind"] == "rescore"
                    else await work.evaluation_review.cancellation(
                        scope, command["id"], run_id, principal
                    )
                )
                row = await work.evaluation_review.result(scope, candidate.result_id)
                if prior is None and (
                    row["revision"] != payload["expected_result_revision"]
                    or await work.evaluation_score.revision(scope, candidate.batch_id)
                    != payload["expected_revision"]
                ):
                    raise ValueError("review_revision_conflict")
            if command["kind"] == "rescore":
                run_id = await self.judge.rescore(
                    scope,
                    candidate,
                    RescoreRequest.model_validate(payload["body"]),
                    downstream_request,
                    principal=principal,
                )
                status = "submitted"
            else:
                await self.judge.cancel(scope, principal, run_id, review_command_id=command["id"])
                status = "cancelling"
        except ExecutionCapacityUnavailable:
            status = "queued"
        except (ValueError, PermissionError) as failure:
            error = (
                "review_revision_conflict"
                if "revision_conflict" in str(failure)
                else "review_authority_unavailable"
                if isinstance(failure, PermissionError)
                else "review_execution_unavailable"
            )
        # Unexpected infrastructure errors leave the lease recoverable. No accepted
        # command is discarded merely because a worker died after JudgeService commit.
        async with self.uow_factory(KERNEL) as work:
            await work.evaluation_review.finish(
                scope, command, status=status, judge_run_id=run_id, error=error
            )
            await work.commit()
