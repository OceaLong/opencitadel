"""Fixed comparison revisions; every returned snapshot passes a fresh authority barrier."""

from copy import deepcopy
from dataclasses import asdict
from itertools import combinations
from uuid import UUID

from app.application.ports.execution_comparison import (
    ComparisonRequest,
    canonical_runs,
    command_request_id,
)
from app.domain.analysis.comparison import (
    apply_manual_alignments,
    retained_step_identities,
    suggest_alignments,
    validate_detail_runs,
)


class ExecutionComparisonService:
    def __init__(self, repository, *, workspace_timezone=None, jobs=None, bodies=None):
        self.repository = repository
        self.jobs = jobs
        self.bodies = bodies
        self.workspace_timezone = workspace_timezone

    @staticmethod
    def _authorize(scope, principal, *, write=False):
        if scope.user_id != principal.user_id:
            raise PermissionError("comparison_scope_mismatch")
        if write and principal.is_auditor:
            raise PermissionError("comparison_read_only_principal")

    @staticmethod
    def _revision(value):
        if type(value) is not int or value < 1:
            raise ValueError("invalid_comparison_revision")
        return value

    async def create(self, scope, principal, payload):
        self._authorize(scope, principal, write=True)
        request = ComparisonRequest.parse(payload, workspace_timezone=self.workspace_timezone)
        identity, revision = await self.repository.materialize(scope, principal, request)
        return await self.get(scope, principal, identity, revision)

    async def refresh(self, scope, principal, comparison_id, payload):
        self._authorize(scope, principal, write=True)
        payload = dict(payload)
        expected = self._revision(payload.pop("expected_revision", None))
        request = ComparisonRequest.parse(payload, workspace_timezone=self.workspace_timezone)
        identity, revision = await self.repository.materialize(
            scope,
            principal,
            request,
            comparison_id=str(UUID(comparison_id)),
            expected_revision=expected,
        )
        return await self.get(scope, principal, identity, revision)

    async def get(
        self,
        scope,
        principal,
        comparison_id,
        revision,
        *,
        cursor=None,
        limit=100,
        detail_run_ids=(),
    ):
        self._authorize(scope, principal)
        identity = str(UUID(comparison_id))
        self._revision(revision)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_comparison_page_limit")
        details = canonical_runs(detail_run_ids)
        if details:
            validate_detail_runs(details)
        for _ in range(3):
            value = await self.repository.read(
                scope,
                principal,
                identity,
                revision,
                cursor=cursor,
                limit=limit,
                detail_run_ids=details,
            )
            body = deepcopy(value.body)
            retained = [
                retained_step_identities(d["body"], f"{identity}:{revision}:{d['run_id']}")
                for d in body.get("details", [])
                if d["availability"] == "available"
            ]
            body["suggestions"] = [
                dict(asdict(a), run_pair=sorted((left[0].run_id, right[0].run_id)))
                for left, right in combinations(retained, 2)
                if left and right
                for a in suggest_alignments(left, right)
            ]
            body["suggestions"] = apply_manual_alignments(
                body["suggestions"],
                body.get("alignments", []),
                [step for run in retained for step in run],
            )
            current = await self.repository.current(scope, principal, identity, revision)
            if value.authority == current:
                return body
        raise PermissionError("comparison_coverage_changed")

    async def align(self, scope, principal, comparison_id, revision, payload):
        self._authorize(scope, principal, write=True)
        self._revision(revision)
        if not isinstance(payload, dict) or set(payload) != {
            "request_id",
            "expected_revision",
            "edits",
        }:
            raise ValueError("invalid_comparison_alignment")
        request_id = command_request_id(payload["request_id"])
        expected, edits = payload["expected_revision"], payload["edits"]
        if type(expected) is not int or expected < 0:
            raise ValueError("invalid_alignment_revision")
        if not isinstance(edits, list) or not 1 <= len(edits) <= 100:
            raise ValueError("invalid_comparison_alignment")
        fields = {"left_run_id", "left_step_id", "right_run_id", "right_step_id", "action"}
        for edit in edits:
            if (
                not isinstance(edit, dict)
                or not fields <= set(edit)
                or set(edit) - fields - {"left_attempt_id", "right_attempt_id"}
                or edit["action"] not in {"confirm", "unpair"}
            ):
                raise ValueError("invalid_comparison_alignment")
            for side in ("left", "right"):
                UUID(edit[side + "_run_id"])
                attempt = edit.get(side + "_attempt_id")
                if attempt is not None and (
                    not isinstance(attempt, str) or not 1 <= len(attempt) <= 255
                ):
                    raise ValueError("invalid_comparison_alignment")
                step = edit[side + "_step_id"]
                if not isinstance(step, str) or not 1 <= len(step) <= 255:
                    raise ValueError("invalid_comparison_alignment")
        identity = str(UUID(comparison_id))
        accepted_revision = await self.repository.align(
            scope,
            principal,
            identity,
            revision,
            expected_revision=expected,
            edits=deepcopy(edits),
            request_id=request_id,
        )
        body = await self.get(scope, principal, identity, revision)
        body["accepted_alignment_revision"] = accepted_revision
        return body

    async def artifact_diff(self, scope, principal, comparison_id, revision, payload):
        self._authorize(scope, principal, write=True)
        self._revision(revision)
        identity = str(UUID(comparison_id))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"request_id", "left", "right", "format"}
            or payload["format"] not in {"text", "json"}
        ):
            raise ValueError("invalid_artifact_selection")
        request_id = command_request_id(payload["request_id"])
        for side in ("left", "right"):
            value = payload[side]
            if not isinstance(value, dict) or set(value) != {
                "run_id",
                "step_id",
                "artifact_id",
                "version",
            }:
                raise ValueError("invalid_artifact_selection")
            if type(value["version"]) is not int or value["version"] < 1:
                raise ValueError("invalid_artifact_selection")
            UUID(value["run_id"])
            for field in ("step_id", "artifact_id"):
                if not isinstance(value[field], str) or not 1 <= len(value[field]) <= 255:
                    raise ValueError("invalid_artifact_selection")
        result = await self.jobs.enqueue(
            scope,
            principal,
            identity,
            revision,
            {key: deepcopy(payload[key]) for key in ("left", "right", "format")},
            request_id=request_id,
        )
        return {"status": "queued", **result}

    async def artifact_diff_page(self, scope, principal, job_id, *, cursor=None):
        self._authorize(scope, principal)
        return await self.jobs.page(scope, principal, str(UUID(job_id)), cursor=cursor)
