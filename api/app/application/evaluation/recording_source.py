"""Complete current-authorized F06 input/output pages at one fixed public cut."""

import json
from uuid import UUID

from app.domain.evaluation.errors import ReplayMismatch
from app.domain.evaluation.recording import MAX_RECORDING_BYTES, MAX_RECORDING_SLOTS


class RecordingSource:
    def __init__(self, content, views):
        self.content, self.views = content, views

    async def complete(self, scope, run_id, step_id, at, kind):
        cursor, seen, parts, identity, size, redacted = None, set(), [], None, 0, False
        while True:
            page = await self.content.read_step_content(
                scope, run_id, step_id, at, cursor, content_kind=kind
            )
            if page.availability != "available" or page.content is None or page.at != at:
                raise ReplayMismatch("source_content_unavailable")
            if identity is not None and page.content_id != identity:
                raise ReplayMismatch("source_identity_changed")
            identity = page.content_id
            size += len(page.content.encode())
            if size > MAX_RECORDING_BYTES:
                raise ReplayMismatch("source_content_too_large")
            parts.append(page.content)
            redacted |= page.redacted
            if not page.truncated:
                if page.next_cursor:
                    raise ReplayMismatch("source_content_incomplete")
                try:
                    return json.loads("".join(parts)), redacted, UUID(identity)
                except (ValueError, TypeError) as error:
                    raise ReplayMismatch("source_content_invalid") from error
            if not page.next_cursor or page.next_cursor in seen:
                raise ReplayMismatch("source_content_incomplete")
            cursor = page.next_cursor
            seen.add(cursor)

    async def steps(self, scope, run_id, at=None):
        view = await self.views.get_view(scope, run_id, at=at)
        if view.run.status not in {"succeeded", "failed", "cancelled", "completed"} or not view.at:
            raise ReplayMismatch("source_not_terminal")
        steps, cursor, seen = [], None, set()
        while True:
            page = await self.views.list_steps(scope, run_id, at=view.at, cursor=cursor, limit=500)
            steps.extend(page.items)
            if len(steps) > MAX_RECORDING_SLOTS:
                raise ReplayMismatch("recording_slot_limit")
            if not page.next_cursor:
                return view.at, steps
            if page.next_cursor in seen:
                raise ReplayMismatch("source_steps_incomplete")
            cursor = page.next_cursor
            seen.add(cursor)
