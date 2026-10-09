"""Durable leased artifact differences; claim transaction ends before source I/O."""

from dataclasses import asdict

from app.domain.analysis.artifact_diff import DiffResult
from app.domain.models.scope import OwnerScope, Principal


async def calculate_artifact_diff(
    reader, compute, scope, principal, comparison_id, revision, selection, *, limit
):
    left = await reader.read(
        scope, principal, comparison_id, revision, selection["left"], limit=limit
    )
    right = await reader.read(
        scope, principal, comparison_id, revision, selection["right"], limit=limit
    )
    kind = selection["format"]
    if left.truncated or right.truncated:
        result = asdict(DiffResult(None, False, "input_limit"))
    else:
        if left.kind == "web" or right.kind == "web":
            kind = "web"
        try:
            left.data.decode("utf-8")
            right.data.decode("utf-8")
        except UnicodeError:
            kind = "binary"
        if kind == "binary":
            result = asdict(
                DiffResult(left.verified_digest != right.verified_digest, True, "metadata_only")
            )
        else:
            result = await compute.compute(left.data, right.data, kind)
    return {
        "left": left.public_metadata,
        "right": right.public_metadata,
        "format": kind,
        "diff": result,
    }


class ComparisonDiffWorker:
    def __init__(self, jobs, reader_factory, compute):
        self.jobs, self.reader_factory, self.compute = jobs, reader_factory, compute

    async def process_pending(self):
        job = await self.jobs.claim()
        if job is None:
            return 0
        try:
            scope = OwnerScope.model_validate(job["scope"])
            principal = Principal.model_validate(job["principal"])
            reader = self.reader_factory(scope, principal)
            result = await calculate_artifact_diff(
                reader,
                self.compute,
                scope,
                principal,
                job["comparison_id"],
                job["revision"],
                job["selection"],
                limit=2097152,
            )
            # Repository rechecks both current fixed selections and lease after I/O.
            await self.jobs.publish(job, scope, principal, result)
        # Provider exceptions fail the owned job closed; cancellation retains its lease.
        except Exception:  # noqa: BLE001
            await self.jobs.fail(job)
        return 1
