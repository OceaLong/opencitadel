"""Human review is independent of automatic execution and model source settlement."""


def review_complete(required: set[str], received: set[str]) -> bool:
    return required.issubset(received)


class ReviewService:
    def __init__(self, suites):
        self.suites = suites

    async def _context(self, scope, principal, result_id, rubric_id=None):
        from app.domain.evaluation.batch import ScoringCandidate
        from app.domain.models.authorization import AuthorizationContext

        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            row = await work.evaluation_review.result(scope, result_id)
        suite = await self.suites.get_version(scope, principal, "suite", row["suite_version"])
        rubric = await self.suites.get_version(
            scope,
            principal,
            "rubric",
            rubric_id or row.get("current_rubric") or suite.rubric_version,
        )
        dataset = await self.suites.datasets.get_version(scope, principal, suite.dataset_version)
        case = next(c for c in dataset.cases if c.id == row["case_revision_id"])
        dimensions = {d.id for d in rubric.dimensions}
        applicable = set(case.applicable_dimensions) or dimensions
        applicable &= dimensions
        mandatory = {c.dimension_id for c in rubric.required_conditions if c.source == "human"}
        original_rubric = (
            rubric
            if rubric.id == suite.rubric_version
            else await self.suites.get_version(scope, principal, "rubric", suite.rubric_version)
        )
        from app.domain.evaluation.review import case_review_requirements

        candidate = ScoringCandidate(
            batch_id=row["batch_id"],
            result_id=row["id"],
            result_revision=row["revision"],
            run_id=row["run_id"],
            run_revision=row["run_revision"],
            suite_version_id=row["suite_version"],
            case_revision_id=row["case_revision_id"],
            config_version_id=row["config_version_id"],
        )
        return (
            row,
            candidate,
            rubric,
            sorted(mandatory & applicable),
            sorted(applicable),
            case_review_requirements(dataset, original_rubric),
        )

    async def current_context(self, scope, principal, result_id):
        from app.domain.evaluation.review import CurrentReviewContext
        from app.domain.models.authorization import AuthorizationContext

        auth = AuthorizationContext.for_principal(principal, scope=scope)
        async with self.suites.uow_factory(auth) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            result = await work.evaluation_review.result(scope, result_id)
        # The case and suite are immutable; only applicability is needed, never private material.
        suite = await self.suites.get_version(scope, principal, "suite", result["suite_version"])
        dataset = await self.suites.datasets.get_version(scope, principal, suite.dataset_version)
        case = next(c for c in dataset.cases if c.id == result["case_revision_id"])
        async with self.suites.uow_factory(auth) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            current = await work.evaluation_review.current_context(scope, result_id)
        dimensions = {d["id"] for d in current["rubric"]["dimensions"]}
        applicable = dimensions & (set(case.applicable_dimensions) or dimensions)
        current.pop("rubric_id")
        current["applicable_dimensions"] = sorted(applicable)
        current["human_heads"] = [h for h in current["human_heads"] if h["dimension"] in applicable]
        return CurrentReviewContext.model_validate(current)

    async def append_score(self, scope, principal, result_id, expected_revision, request_id, score):
        from app.domain.evaluation.review import HumanReview

        score = HumanReview.model_validate(score)
        return await self._submit(
            scope,
            principal,
            result_id,
            "human",
            request_id,
            expected_revision,
            score.expected_result_revision,
            score.model_dump(mode="json"),
        )

    async def rescore(self, scope, principal, result_id, request, request_id):
        from app.domain.evaluation.judge_protocol import RescoreRequest

        request = RescoreRequest.model_validate(request)
        return await self._submit(
            scope,
            principal,
            result_id,
            "rescore",
            request_id,
            request.expected_evaluation_revision,
            request.expected_result_revision,
            request.model_dump(mode="json"),
        )

    async def cancel(
        self,
        scope,
        principal,
        result_id,
        run_id,
        expected_revision,
        expected_result_revision,
        request_id,
    ):
        return await self._submit(
            scope,
            principal,
            result_id,
            "cancel",
            request_id,
            expected_revision,
            expected_result_revision,
            {"judge_run_id": str(run_id)},
        )

    async def _submit(
        self,
        scope,
        principal,
        result_id,
        kind,
        request_id,
        expected_revision,
        expected_result_revision,
        body,
    ):
        from uuid import UUID

        from app.domain.evaluation.configuration import digest
        from app.domain.evaluation.errors import DatasetConflict
        from app.domain.evaluation.review import ReviewReceipt
        from app.domain.models.authorization import AuthorizationContext

        if (
            type(expected_revision) is not int
            or expected_revision < 0
            or not request_id.strip()
            or len(request_id) > 255
        ):
            raise ValueError("invalid_review_request")
        auth = AuthorizationContext.for_principal(principal, scope=scope, request_id=request_id)
        fingerprint = digest(
            {
                "kind": kind,
                "actor": principal.model_dump(mode="json"),
                "result": str(result_id),
                "expected_revision": expected_revision,
                "expected_result_revision": expected_result_revision,
                "body": body,
            }
        )
        async with self.suites.uow_factory(auth) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            prior = await work.evaluation_review.prior(
                scope, result_id, kind, request_id, fingerprint
            )
            if prior is not None:
                return ReviewReceipt.model_validate(prior)
        _row, candidate, rubric, required, applicable, batch_required = await self._context(
            scope,
            principal,
            result_id,
            UUID(body["rubric_version"]) if "rubric_version" in body else None,
        )
        if candidate.result_revision != expected_result_revision:
            raise DatasetConflict("review_revision_conflict")
        if kind == "rescore" and str(rubric.judge_config_version) != body["judge_config_version"]:
            raise ValueError("judge_configuration_mismatch")
        if kind == "human" and any(s["dimension"] not in applicable for s in body["scores"]):
            raise ValueError("invalid_review_dimension")
        source = None
        if kind == "rescore":
            from app.domain.models.scope import Principal

            async with self.suites.uow_factory(auth) as work:
                batch = await work.evaluation_batch.get(scope, candidate.batch_id)
                original = Principal.model_validate(batch["principal"])
            original_scope = scope.model_copy(update={"user_id": original.user_id})
            suite = await self.suites.get_version(
                original_scope, original, "suite", candidate.suite_version_id
            )
            dataset = await self.suites.datasets.get_version(
                original_scope, original, suite.dataset_version
            )
            case = next(c for c in dataset.cases if c.id == candidate.case_revision_id)
            configs = tuple(
                [
                    await self.suites.get_version(original_scope, original, "config", identity)
                    for identity in dict.fromkeys(
                        (candidate.config_version_id, UUID(body["judge_config_version"]))
                    )
                ]
            )
            source = (
                original_scope,
                original,
                suite,
                case,
                configs,
                await self.suites.policies.load_active_pair(),
            )
        payload = {
            "scope": ("team:" + scope.team_id if scope.team_id else "user:" + scope.user_id),
            "principal": principal.model_dump(mode="json"),
            "result_id": str(result_id),
            "kind": kind,
            "request_id": request_id,
            "fingerprint": fingerprint,
            "expected_revision": expected_revision,
            "expected_result_revision": expected_result_revision,
            "candidate": candidate.model_dump(mode="json"),
            "rubric_version": str(rubric.id),
            "required": required,
            "applicable": applicable,
            "batch_required": batch_required,
            "body": body,
            "scores": body.get("scores", []),
            "judge_run_id": body.get("judge_run_id"),
        }
        async with self.suites.uow_factory(auth) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            if source is not None:
                from app.application.evaluation.preflight import validate_current_config
                from app.domain.models.resource_pin import ResourceUnavailable

                original_scope, original, suite, case, configs, pair = source
                await work.evaluation_review.lock_pins(
                    original_scope, "dataset_version", str(suite.dataset_version)
                )
                pins = await work.resource_pins.validate(
                    original_scope,
                    "dataset_version",
                    str(suite.dataset_version),
                    suite.dataset_proof.resources,
                )
                if any(not pin.available for pin in pins):
                    raise ResourceUnavailable("review_source_unavailable")
                for resource in (*suite.dataset_proof.resources, *case.resources):
                    await work.resource_pins.resolve(original_scope, resource, lock=True)
                for config in configs:
                    await work.evaluation_review.lock_config(original_scope, config.selection)
                    current = await self.suites.resolved(
                        work, original_scope, config.selection, original, policy_pair=pair
                    )
                    if validate_current_config(config, current) or (
                        not current["credential_configured"]
                        and current["identity"]["provider"] != "ollama"
                    ):
                        raise ResourceUnavailable("review_configuration_unavailable")
                    await work.evaluation_review.lock_pins(
                        original_scope, "config_version", str(config.id)
                    )
                    pins = await work.resource_pins.validate(
                        original_scope, "config_version", str(config.id), config.selection.resources
                    )
                    if any(not pin.available for pin in pins):
                        raise ResourceUnavailable("review_source_unavailable")
                    for resource in config.selection.resources:
                        await work.resource_pins.resolve(original_scope, resource, lock=True)
                payload["source_principal"] = original.model_dump(mode="json")
                payload["source_content"] = await work.evaluation_review.source_content(
                    original_scope, candidate.run_id
                )
            # Fixed resource authority is checked before the signed write. Kernel-only
            # physical facts stay behind the existing scoped boolean effect reader.
            if kind != "cancel" and await work.evaluation_batch.unknown_effect(
                scope, candidate.run_id, include_unresolved=True, principal=principal
            ):
                raise ValueError("review_result_unavailable")
            for raw in [e for score in body.get("scores", []) for e in score["evidence"]]:
                from app.domain.models.resource_pin import ResourceIdentity

                await work.resource_pins.resolve(
                    scope, ResourceIdentity.model_validate(raw), lock=True
                )
            result = await work.evaluation_review.submit(payload, authorization=auth)
            await work.commit()
            return ReviewReceipt.model_validate(result)

    async def get_command(self, scope, principal, command_id):
        from app.domain.evaluation.review import ReviewReceipt
        from app.domain.models.authorization import AuthorizationContext

        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            return ReviewReceipt.model_validate(await work.evaluation_review.get(scope, command_id))

    async def list_pending(
        self, scope, cursor=None, limit=50, *, principal, status="pending", rubric_id=None
    ):
        import base64
        import hashlib
        import hmac
        import json
        from uuid import UUID

        from app.domain.evaluation.review import ReviewItem, ReviewPage
        from app.domain.models.authorization import AuthorizationContext

        if (
            type(limit) is not int
            or not 1 <= limit <= 200
            or status not in {"pending", "complete", "not_required", "all"}
        ):
            raise ValueError("invalid_review_query")
        context = [
            scope.model_dump(mode="json"),
            principal.user_id,
            status,
            str(rubric_id) if rubric_id else None,
        ]
        after = None
        secret = self.suites.cursor_secret
        if cursor:
            try:
                if len(cursor) > 4096:
                    raise ValueError()
                raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                body, signature = raw[:-32], raw[-32:]
                decoded = json.loads(body)
                if (
                    not hmac.compare_digest(
                        signature, hmac.new(secret, body, hashlib.sha256).digest()
                    )
                    or decoded["context"] != context
                ):
                    raise ValueError()
                after = str(UUID(decoded["after"]))
            except (ValueError, KeyError, TypeError):
                raise ValueError("invalid_cursor") from None
        auth = AuthorizationContext.for_principal(principal, scope=scope)
        async with self.suites.uow_factory(auth) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            rows = await work.evaluation_review.rows(scope, after=after, limit=limit)
        items = []
        for raw in rows:
            row, candidate, rubric, required, _, _ = await self._context(
                scope, principal, raw["id"]
            )
            if rubric_id and rubric.id != rubric_id:
                # An override is visible only once a bound score set actually exists.
                async with self.suites.uow_factory(auth) as work:
                    revision = await work.evaluation_score.revision(scope, candidate.batch_id)
                    history = await work.evaluation_score.history(
                        scope,
                        candidate.batch_id,
                        evaluation_revision=revision,
                        result_id=candidate.result_id,
                    )
                    if not any(
                        s.result_id == candidate.result_id and s.score.rubric_revision == rubric_id
                        for s in history
                    ):
                        continue
                row, candidate, rubric, required, _, _ = await self._context(
                    scope, principal, raw["id"], rubric_id
                )
            async with self.suites.uow_factory(auth) as work:
                await work.evaluation_dataset.authorize(scope, principal, write=False)
                revision = await work.evaluation_score.revision(scope, candidate.batch_id)
                history = await work.evaluation_score.history(
                    scope,
                    candidate.batch_id,
                    evaluation_revision=revision,
                    result_id=candidate.result_id,
                )
                latest = {}
                for value in history:
                    if (
                        value.result_id == candidate.result_id
                        and value.score.rubric_revision == rubric.id
                        and value.score.source == "human"
                    ):
                        latest[value.score.dimension] = value
                received = {d for d, score in latest.items() if score.score.status == "valid"}
                review = (
                    ("complete" if review_complete(set(required), received) else "pending")
                    if required or latest
                    else "not_required"
                )
                if status == "all" or review == status:
                    items.append(
                        ReviewItem(
                            result_id=candidate.result_id,
                            batch_id=candidate.batch_id,
                            run_id=candidate.run_id,
                            rubric_version=rubric.id,
                            result_revision=candidate.result_revision,
                            evaluation_revision=revision,
                            execution_status=row["execution_status"],
                            scoring_status=row["scoring_status"],
                            review_status=review,
                            required_dimensions=tuple(required),
                            received_dimensions=tuple(sorted(received)),
                        )
                    )
        next_cursor = None
        if len(rows) == limit:
            body = json.dumps(
                {"context": context, "after": str(rows[-1]["id"])}, separators=(",", ":")
            ).encode()
            next_cursor = (
                base64.urlsafe_b64encode(body + hmac.new(secret, body, hashlib.sha256).digest())
                .decode()
                .rstrip("=")
            )
        return ReviewPage(items=tuple(items), next_cursor=next_cursor)

    async def history(self, scope, principal, result_id, *, evaluation_revision=None):
        from app.domain.models.authorization import AuthorizationContext

        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            row = await work.evaluation_review.result(scope, result_id)
            revision = (
                evaluation_revision
                if evaluation_revision is not None
                else await work.evaluation_score.revision(scope, row["batch_id"])
            )
            return tuple(
                s
                for s in await work.evaluation_score.history(
                    scope, row["batch_id"], evaluation_revision=revision, result_id=result_id
                )
                if s.result_id == result_id
            )

    async def history_page(
        self, scope, principal, result_id, *, evaluation_revision=None, cursor=None, limit=50
    ):
        import base64
        import hashlib
        import hmac
        import json

        from app.domain.evaluation.review import ScoreHistoryPage
        from app.domain.models.authorization import AuthorizationContext

        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        context = [
            "review-score-history",
            scope.model_dump(mode="json"),
            principal.user_id,
            str(result_id),
        ]
        secret = self.suites.cursor_secret
        after_revision, after_dimension = 0, ""
        if cursor:
            try:
                if len(cursor) > 4096:
                    raise ValueError()
                raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                body, signature = raw[:-32], raw[-32:]
                decoded = json.loads(body)
                if decoded["context"] != context or not hmac.compare_digest(
                    signature, hmac.new(secret, body, hashlib.sha256).digest()
                ):
                    raise ValueError()
                if evaluation_revision is not None and evaluation_revision != decoded["revision"]:
                    raise ValueError()
                evaluation_revision = decoded["revision"]
                after_revision, after_dimension = decoded["after"]
                if (
                    type(after_revision) is not int
                    or after_revision < 0
                    or not isinstance(after_dimension, str)
                ):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise ValueError("invalid_cursor") from None
        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            row = await work.evaluation_review.result(scope, result_id)
            revision = (
                evaluation_revision
                if evaluation_revision is not None
                else await work.evaluation_score.revision(scope, row["batch_id"])
            )
            values = await work.evaluation_score.history(
                scope,
                row["batch_id"],
                evaluation_revision=revision,
                result_id=result_id,
                after_revision=after_revision,
                after_dimension=after_dimension,
                limit=limit + 1,
            )
            invalidations = await work.evaluation_summary.invalidations(
                scope, principal, row["batch_id"], evaluation_revision=revision
            )
        next_cursor = None
        if len(values) > limit:
            last = values[limit - 1]
            body = json.dumps(
                {
                    "context": context,
                    "revision": revision,
                    "after": [last.evaluation_revision, last.score.dimension],
                },
                separators=(",", ":"),
            ).encode()
            next_cursor = (
                base64.urlsafe_b64encode(body + hmac.new(secret, body, hashlib.sha256).digest())
                .decode()
                .rstrip("=")
            )
        return ScoreHistoryPage(
            items=values[:limit],
            evaluation_revision=revision,
            next_cursor=next_cursor,
            invalidations=invalidations,
        )
