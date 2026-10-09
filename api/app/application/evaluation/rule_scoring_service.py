"""Durable rule consumer; E08 owns the independently pending model source."""

from app.application.evaluation.rule_evaluator import IsolatedRuleEvaluator
from app.domain.evaluation.scoring import ScoringProjectionAdvanced
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.resource_pin import ResourceIdentity
from app.domain.models.scope import Principal


class RuleScoringService:
    def __init__(self, uow_factory, suites, evidence, *, evaluator=None):
        self.uow_factory, self.suites, self.evidence = uow_factory, suites, evidence
        self.evaluator = evaluator or IsolatedRuleEvaluator()

    async def score(self, scope, candidate):
        kernel = AuthorizationContext.system("execution-kernel")
        async with self.uow_factory(kernel) as work:
            batch = await work.evaluation_batch.get(scope, candidate.batch_id)
            principal = Principal.model_validate(batch["principal"])
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            revision = await work.evaluation_score.revision(scope, candidate.batch_id)
            if await work.evaluation_score.settled(scope, candidate.result_id, "rule") is not None:
                return revision
            await work.evaluation_score.eligible(scope, principal, candidate)
        suite = await self.suites.get_version(scope, principal, "suite", candidate.suite_version_id)
        rubric = await self.suites.get_version(scope, principal, "rubric", suite.rubric_version)
        async with self.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            dataset = await work.evaluation_dataset.get_version(scope, suite.dataset_version)
            members = [m for m in dataset["members"] if m["id"] == candidate.case_revision_id]
        # Exact manifest bytes, outside a held UoW. Missing resource pins are evaluated
        # independently below, rather than converting unavailable evidence to a zero.
        cases = await self.suites.datasets._cases(members)
        case = next((c for c in cases if c.id == candidate.case_revision_id), None)
        if case is None:
            raise ValueError("scoring_case_unavailable")
        resources = tuple(case.resources) + tuple(
            ResourceIdentity.model_validate(s)
            for rule in case.rules
            for s in rule.get("sources", ())
        )
        evidence = await self.evidence.read(scope, principal, candidate, resources=resources)
        applicable = tuple(case.applicable_dimensions) or tuple(d.id for d in rubric.dimensions)
        scores = []
        for index, rule in enumerate(case.rules):
            # Cancellation/revocation between expensive rules stops subsequent admission.
            async with self.uow_factory(kernel) as work:
                await work.evaluation_score.eligible(scope, principal, candidate)
            score = await self.evaluator.evaluate(
                rule,
                evidence.subject,
                case.reference_answer if case.reference_confirmed else None,
                evidence.evidence,
            )
            scores.append(
                score.model_copy(
                    update={
                        "dimension": f"rule:{index}",
                        "rubric_revision": rubric.id,
                        "evidence": evidence.resources,
                        "recording": evidence.evidence.recording,
                    }
                )
            )
        required = tuple(f"rule:{i}" for i, r in enumerate(case.rules) if r.get("required", True))
        async with self.uow_factory(kernel) as work:
            # Idempotent source key, deterministic rules. No held lock during bodies or network.
            revision = await work.evaluation_score.append(
                scope,
                principal,
                candidate,
                source="rule",
                scores=scores,
                expected_evaluation_revision=revision,
                request_id=f"rule:{candidate.result_id}:{candidate.run_revision}",
                required_dimensions=required,
                applicable_dimensions=applicable,
            )
            await work.commit()
            return revision

    async def score_batch(self, scope, batch_id, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_score_limit")
        async with self.uow_factory(AuthorizationContext.system("execution-kernel")) as work:
            candidates = await work.evaluation_batch.scoring_candidates(scope, batch_id, limit=5000)
            pending = []
            for candidate in candidates:
                if await work.evaluation_score.settled(scope, candidate.result_id, "rule") is None:
                    pending.append(candidate)
                    if len(pending) >= limit:
                        break
        completed = 0
        for candidate in pending:
            try:
                await self.score(scope, candidate)
                completed += 1
            except ScoringProjectionAdvanced:
                # Re-read the completed subject cut on the next automatic tick.
                continue
            except ValueError as error:
                if str(error) not in {
                    "scoring_candidate_stale",
                    "evaluation_revision_conflict",
                    "scoring_effect_unresolved",
                }:
                    raise
        return completed
