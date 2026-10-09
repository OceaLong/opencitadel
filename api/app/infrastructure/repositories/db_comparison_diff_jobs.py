"""Kernel-only bounded leases; tenant operations use the saved actual caller."""

import json

from sqlalchemy import text

from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.security.db_authorization import configure_session_authorization


def diff_pages(result):
    encoded = json.dumps(
        result, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()
    if len(encoded) > 16 * (65536 - 3):
        result = {
            **result,
            "diff": {
                "content_changed": result["diff"]["content_changed"],
                "complete": False,
                "reason": "output_limit",
                "content": "",
                "operations": [],
            },
        }
        encoded = json.dumps(
            result, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    if len(encoded) > 16 * (65536 - 3):
        raise ValueError("output_limit")
    pages = []
    while encoded:
        end = min(65536, len(encoded))
        while True:
            try:
                page = encoded[:end].decode()
                break
            except UnicodeError:
                end -= 1
        pages.append(page)
        encoded = encoded[end:]
    return result, pages


class DBComparisonDiffJobs:
    def __init__(self, comparisons):
        self.comparisons = comparisons

    async def _kernel(self, *, job=None):
        repo = self.comparisons
        async with repo.session_factory() as db:
            await configure_session_authorization(
                db, AuthorizationContext.system("execution-kernel"), signing_secret=repo.secret
            )
            value = await db.scalar(
                text("SELECT public.opencitadel_comparison_diff_claim(:id,:lease)"),
                {"id": job["id"] if job else None, "lease": job["lease_token"] if job else None},
            )
            await db.commit()
            return value

    async def claim(self):
        return await self._kernel()

    async def fail(self, job):
        await self._kernel(job=job)

    async def enqueue(self, scope, principal, comparison_id, revision, selection, *, request_id):
        return await self.comparisons.mutate(
            scope,
            principal,
            "jobs",
            "enqueue",
            comparison_id=comparison_id,
            revision=revision,
            selection=selection,
            request_id=request_id,
        )

    async def publish(self, job, scope, principal, result):
        result, pages = diff_pages(result)
        repo = self.comparisons
        head = {k: result["diff"][k] for k in ("complete", "reason", "content_changed")}
        head.update(format=result["format"], page_count=len(pages))
        async with repo.transactions.transaction(scope, principal) as db:
            await repo._operation(
                db,
                scope,
                principal,
                "jobs",
                "publish",
                job_id=job["id"],
                lease_token=job["lease_token"],
                head=head,
                pages=pages,
            )

    async def page(self, scope, principal, job_id, *, cursor=None):
        repo = self.comparisons
        page = 0 if cursor is None else repo._after(scope, principal, job_id, 0, cursor)
        async with repo.transactions.transaction(scope, principal, current=True) as db:
            result = await repo._operation(
                db, scope, principal, "jobs", "page", job_id=job_id, page=page
            )
        async with repo.transactions.transaction(scope, principal, current=True) as db:
            await repo._operation(db, scope, principal, "jobs", "authorize", job_id=job_id)
        next_page = result.pop("next_page")
        result["next_cursor"] = (
            repo._cursor(scope, principal, job_id, 0, next_page) if next_page is not None else None
        )
        return result
