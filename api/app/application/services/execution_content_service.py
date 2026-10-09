"""Authorized immutable execution content readers."""

import base64
import hashlib
import hmac
import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.application.dto.execution_view import CitationReference
from app.application.execution.content_sanitization import PUBLIC_CONTENT_POLICY, sanitize_content
from app.application.ports.execution_view import ViewCursorInvalid, ViewNotFound
from app.domain.models.resource_pin import ResourceUnavailable


def citation_locator(citation: dict) -> tuple[str, str | int | None]:
    if citation.get("chunk_id"):
        return "chunk", citation["chunk_id"]
    if citation.get("page_no") is not None:
        return "page", citation["page_no"]
    return "document", None


class ContentPage(BaseModel):
    availability: Literal["available", "unavailable"]
    content: str | None = None
    content_type: str = "text/plain"
    content_id: str | None = None
    artifact_id: str | None = None
    version: int | None = None
    redacted: bool = False
    # source_locator_unavailable only follows successful current ownership and fixed revision validation.
    reason: str | None = None
    source_title: str | None = None
    truncated: bool = False
    next_cursor: str | None = None
    at: str | None = None


def _limit_bytes(value):
    if type(value) is not int or not 4 <= value <= 65536:
        raise ViewCursorInvalid("limit_bytes must be between 4 and 65536")


def _utf8_slice(data, offset, limit):
    try:
        data.decode("utf-8")
        if type(offset) is not int or offset < 0 or offset > len(data):
            raise ValueError
        data[:offset].decode("utf-8")
        end = min(len(data), offset + limit)
        while end > offset:
            try:
                return data[offset:end].decode("utf-8"), end
            except UnicodeDecodeError:
                end -= 1
        return "", end
    except (UnicodeError, ValueError) as error:
        raise ResourceUnavailable("invalid immutable UTF-8 content or offset") from error


class ExecutionContentService:
    def __init__(self, uow_factory, views, artifacts, *, cursor_secret: bytes, files=None):
        if len(cursor_secret) < 16:
            raise ValueError("cursor secret too short")
        self.uow_factory = uow_factory
        self.views = views
        self.artifacts = artifacts
        self.files = files
        self.secret = cursor_secret

    def _cursor(self, scope, identity, state):
        value = {
            "v": 1,
            "scope": ("team:" + scope.team_id if scope.team_id else "user:" + scope.user_id),
            "identity": identity,
            "state": state,
        }
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return (
            base64.urlsafe_b64encode(raw + hmac.digest(self.secret, raw, "sha256"))
            .decode()
            .rstrip("=")
        )

    def _state(self, scope, identity, cursor):
        if cursor is None:
            return {"offset": 0}
        try:
            if not isinstance(cursor, str) or len(cursor) > 32768:
                raise ValueError
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            body, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self.secret, body, "sha256")):
                raise ValueError
            value = json.loads(body)
            expected = {
                "v": 1,
                "scope": ("team:" + scope.team_id if scope.team_id else "user:" + scope.user_id),
                "identity": identity,
            }
            if any(value.get(k) != expected[k] for k in ("v", "scope", "identity")):
                raise ValueError
            state = value["state"]
            if type(state.get("offset")) is not int or state["offset"] < 0:
                raise ValueError
            return state
        except (ValueError, KeyError, TypeError, UnicodeError) as error:
            raise ViewCursorInvalid("content cursor does not match selection") from error

    def _page(self, scope, identity, data, state, limit_bytes, **fields):
        digest = hashlib.sha256(data).hexdigest()
        if state.get("digest", digest) != digest:
            raise ResourceUnavailable("immutable content changed")
        content, end = _utf8_slice(data, state["offset"], limit_bytes)
        more = end < len(data)
        return ContentPage(
            availability="available",
            content=content,
            truncated=more,
            next_cursor=self._cursor(scope, identity, {"offset": end, "digest": digest})
            if more
            else None,
            **fields,
        )

    async def read_step_content(
        self, scope, run_id, step_id, at, cursor=None, limit_bytes=65536, *, content_kind="output"
    ):
        _limit_bytes(limit_bytes)
        if content_kind not in ("input", "output"):
            raise ViewCursorInvalid("invalid content kind")
        # Validate foreign/tampered selection before body storage; F04 remains sole at decoder.
        initial = {
            "kind": "step",
            "run": str(run_id),
            "step": step_id,
            "at": at,
            "content_kind": content_kind,
            "public_content_policy": PUBLIC_CONTENT_POLICY,
        }
        state = self._state(scope, initial, cursor)
        cut = await self.views.get_step_cut(scope, run_id, step_id, at)
        ref = getattr(cut.step, content_kind + "_ref")
        if ref is None or ref.availability != "available":
            return ContentPage(
                availability="unavailable", reason="historical_content_unavailable", at=cut.at
            )
        async with self.uow_factory() as uow:
            metadata = await uow.execution_content.get_snapshot(
                scope,
                ref.content_id,
                run_id,
                cut.step.step_id,
                cut.boundary.formal_position,
                include_body=False,
            )
            if metadata is None:
                raise ViewNotFound("content unavailable")
            for citation in metadata["citation_refs"]:
                await self._source_authority(uow, scope, citation)
            row = await uow.execution_content.get_snapshot(
                scope, ref.content_id, run_id, cut.step.step_id, cut.boundary.formal_position
            )
        if row is None:
            raise ViewNotFound("content unavailable")
        data = row["body"].encode()
        if hashlib.sha256(data).hexdigest() != row["content_digest"]:
            raise ResourceUnavailable("immutable content changed")
        try:
            decoded = json.loads(row["body"])
            sanitized = sanitize_content(decoded)
        except (ValueError, RecursionError) as error:
            raise ResourceUnavailable("historical content cannot be safely rendered") from error
        changed = sanitized != decoded
        if changed:
            data = json.dumps(
                sanitized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        return self._page(
            scope,
            initial,
            data,
            state,
            limit_bytes,
            content_id=ref.content_id,
            content_type="application/json",
            redacted=row["redacted"] or changed,
            at=cut.at,
        )

    async def get_provenance(self, scope, artifact_id, version, *, allow_unknown=False):
        if type(version) is not int or version < 1:
            raise ViewNotFound("artifact version unavailable")
        async with self.uow_factory() as uow:
            artifact = await uow.artifact.get_by_id(artifact_id)
            if artifact is None or version > len(artifact.version_refs):
                raise ViewNotFound("artifact version unavailable")
            if await uow.session.get_metadata(artifact.session_id, scope=scope) is None:
                raise ViewNotFound("artifact version unavailable")
            rows = await uow.artifact_provenance.get_version(scope, artifact_id, version)
        if not rows and not allow_unknown:
            raise ViewNotFound("artifact provenance unavailable")
        return rows

    async def get_public_provenance(self, scope, artifact_id, version, *, run_id=None, at=None):
        if (run_id is None) != (at is None):
            raise ViewCursorInvalid("provenance requires both run and at")
        rows = await self.get_provenance(scope, artifact_id, version, allow_unknown=True)
        observations = {}
        if run_id is not None:
            boundary, observations = await self.views.get_production_cut(scope, run_id, at, rows)
            rows = [
                row
                for row in rows
                if row.binding_status == "bound"
                and row.producer_run_id == run_id
                and row.boundary is not None
                and row.boundary <= boundary.formal_position
            ]
        result = []
        for row in rows:
            # Canonical stored refs only. Malformed legacy refs cannot become guessed aliases.
            citations = []
            for ref in row.citation_refs:
                try:
                    citations.append(CitationReference.model_validate(ref).model_dump())
                except (ValidationError, TypeError):
                    continue
            public = row.model_dump(
                exclude={
                    "id",
                    "producer_identity",
                    "evidence",
                    "content_digest",
                    "content_ref",
                    "boundary",
                    "citation_refs",
                }
            )
            public["citation_refs"] = citations
            public.update(observations.get(str(row.produced_event_id), {}))
            result.append(public)
        return result

    async def read_artifact(
        self,
        scope,
        artifact_id,
        version,
        *,
        cursor=None,
        limit_bytes=65536,
        run_id=None,
        step_id=None,
        at=None,
    ):
        """Exact authorized bytes as text; no presentation transformation."""
        return await self._read_artifact(
            scope,
            artifact_id,
            version,
            cursor=cursor,
            limit_bytes=limit_bytes,
            run_id=run_id,
            step_id=step_id,
            at=at,
        )

    async def read_artifact_preview(
        self,
        scope,
        artifact_id,
        version,
        *,
        cursor=None,
        limit_bytes=65536,
        run_id=None,
        step_id=None,
        at=None,
        complete=False,
    ):
        """Sanitize the complete verified artifact before preview pagination."""
        if complete and (cursor is not None or any((run_id, step_id, at))):
            raise ViewCursorInvalid("complete preview requires a current fixed-version selection")
        return await self._read_artifact(
            scope,
            artifact_id,
            version,
            cursor=cursor,
            limit_bytes=limit_bytes,
            run_id=run_id,
            step_id=step_id,
            at=at,
            presentation=True,
            complete=complete,
        )

    async def _read_artifact(
        self,
        scope,
        artifact_id,
        version,
        *,
        cursor=None,
        limit_bytes=65536,
        run_id=None,
        step_id=None,
        at=None,
        presentation=False,
        complete=False,
    ):
        _limit_bytes(limit_bytes)
        identity = {
            "kind": "artifact",
            "id": artifact_id,
            "version": version,
            "run": str(run_id) if run_id else None,
            "step": step_id,
            "at": at,
        }
        if presentation:
            identity["presentation"] = "safe-html-v1"
        state = self._state(scope, identity, cursor)
        rows = await self.get_provenance(
            scope,
            artifact_id,
            version,
            allow_unknown=at is None and run_id is None and step_id is None,
        )
        if at is not None or run_id is not None or step_id is not None:
            if not at or not run_id or not step_id:
                raise ViewCursorInvalid("historical artifact requires run,step,at")
            cut = await self.views.get_step_cut(scope, run_id, step_id, at)
            if not any(
                r.artifact_id == artifact_id
                and r.version == version
                and r.availability == "available"
                for r in (cut.step.artifact_refs or [])
            ):
                raise ViewNotFound("artifact not present at selected step")
            rows = [
                r
                for r in rows
                if r.binding_status == "bound"
                and r.producer_run_id == run_id
                and cut.step.step_id in r.producer_step_ids
                and r.boundary is not None
                and r.boundary <= cut.boundary.formal_position
            ]
            if not rows:
                raise ViewNotFound("artifact producer not present at selected cut")
        if any(r.availability == "unavailable" for r in rows):
            raise ResourceUnavailable("artifact unavailable")
        artifact = await self.artifacts.get_by_id(artifact_id, scope=scope)
        if artifact is None:
            raise ViewNotFound("artifact unavailable")
        content_type = "text/html" if artifact.kind == "web" else "text/markdown"
        data = await self.artifacts.get_content(
            artifact_id, version_index=version, scope=scope, sanitize_html=False
        )
        digest = hashlib.sha256(data).hexdigest()
        if any(r.content_digest and r.content_digest != "sha256:" + digest for r in rows):
            raise ResourceUnavailable("immutable artifact changed")
        redacted = False
        if presentation:
            from app.application.services.artifact_service import sanitize_html_for_preview

            if artifact.kind == "web":
                try:
                    safe = sanitize_html_for_preview(data.decode("utf-8")).encode("utf-8")
                except UnicodeError as error:
                    raise ResourceUnavailable("artifact is not valid UTF-8") from error
                redacted = safe != data
                data = safe
        if complete:
            rendered, _ = _utf8_slice(data, 0, len(data))
            return ContentPage(
                availability="available",
                content=rendered,
                artifact_id=artifact_id,
                version=version,
                content_type=content_type,
                redacted=redacted,
            )
        return self._page(
            scope,
            identity,
            data,
            state,
            limit_bytes,
            redacted=redacted,
            artifact_id=artifact_id,
            version=version,
            content_type=content_type,
            at=at,
        )

    async def _source_authority(self, uow, scope, citation):
        from app.domain.services.content_source_authority import content_source_available

        if not await content_source_available(
            scope,
            citation,
            file_repository=getattr(uow, "file", None),
            knowledge_repository=getattr(uow, "knowledge_base", None),
        ):
            raise ViewNotFound("source unavailable")

    async def read_source(self, scope, citation, *, cursor=None, limit_bytes=65536):
        _limit_bytes(limit_bytes)
        citation = (
            citation.model_dump(mode="json") if hasattr(citation, "model_dump") else dict(citation)
        )
        identity = {"kind": "citation", "citation": citation}
        state = self._state(scope, identity, cursor)
        async with self.uow_factory() as uow:
            canonical = await uow.execution_content.get_citation(scope, citation.get("citation_id"))
            if canonical is None:
                raise ViewNotFound("source citation unavailable")
            for key in (
                "resource_kind",
                "knowledge_base_id",
                "version_id",
                "doc_id",
                "document_revision_id",
                "file_id",
                "content_digest",
                "object_identity",
            ):
                if key in citation and citation[key] != canonical.get(key):
                    raise ViewNotFound("source citation unavailable")
            for key in ("chunk_id", "page_no"):
                if citation.get(key) is not None and citation[key] != canonical.get(key):
                    raise ViewNotFound("source locator unavailable")
            selected = {
                **canonical,
                "chunk_id": citation.get("chunk_id", canonical.get("chunk_id")),
                "page_no": citation.get("page_no", canonical.get("page_no")),
            }
            citation = selected
            await self._source_authority(uow, scope, citation)
            if citation.get("resource_kind") == "file":
                if not self.files or not all(
                    citation.get(k) for k in ("file_id", "content_digest", "object_identity")
                ):
                    raise ResourceUnavailable("immutable attachment unavailable")
                file_ref = (
                    citation["file_id"],
                    citation["content_digest"],
                    citation["object_identity"],
                )
            else:
                file_ref = None
        if file_ref is not None:
            data = await self.files.read_fixed(*file_ref, scope)
            return self._page(scope, identity, data, state, limit_bytes)
        async with self.uow_factory() as uow:
            kb = citation.get("knowledge_base_id")
            version = citation.get("version_id")
            doc = citation.get("doc_id")
            revision = citation.get("document_revision_id")
            if not all((kb, version, doc, revision)):
                raise ResourceUnavailable("historical source identity unavailable")
            owned = await uow.knowledge_base.get_kb(kb, scope=scope)
            if owned is None:
                raise ViewNotFound("source unavailable")
            resolved = await uow.knowledge_base.get_document_for_version(kb, version, doc)
            if resolved is None or resolved[1] != revision:
                raise ResourceUnavailable("fixed document revision unavailable")
            source_title = resolved[0].title
            kind, locator = citation_locator(citation)
            if kind == "chunk":
                chunks = await uow.knowledge_base.get_chunks_by_ids_for_version(
                    kb, version, [locator]
                )
                match = [
                    r
                    for r in chunks
                    if r.chunk.id == locator
                    and r.chunk.kb_id == kb
                    and r.chunk.version_id == version
                    and r.chunk.doc_id == doc
                    and r.document_revision_id == revision
                    and (citation.get("page_no") is None or r.chunk.page_no == citation["page_no"])
                ]
                if not match:
                    # Recheck after locator I/O. Only a missing locator is recoverable;
                    # deleted source/version, revision drift and authorization loss still fail closed.
                    await self._source_authority(uow, scope, citation)
                    return ContentPage(
                        availability="unavailable",
                        reason="source_locator_unavailable",
                        source_title=source_title,
                    )
                if len(match) != 1:
                    raise ResourceUnavailable("ambiguous fixed chunk")
                return self._page(
                    scope,
                    identity,
                    match[0].chunk.content.encode(),
                    state,
                    limit_bytes,
                    source_title=source_title,
                )
            # At most one immutable parent chunk is fetched per repository page.
            output = []
            remaining = limit_bytes
            item_cursor = state.get("item_cursor")
            offset = state["offset"]
            while remaining >= 4:
                page = await uow.knowledge_base.read_document_page_for_version(
                    kb,
                    version,
                    doc,
                    revision,
                    page_no=locator if kind == "page" else None,
                    cursor=item_cursor,
                    limit=1,
                )
                if not page.items:
                    return ContentPage(
                        availability="available", content="".join(output), source_title=source_title
                    )
                data = (page.items[0].content + ("\n\n" if page.next_cursor else "")).encode()
                digest = hashlib.sha256(data).hexdigest()
                if state.get("digest", digest) != digest:
                    raise ResourceUnavailable("immutable document changed")
                segment, end = _utf8_slice(data, offset, remaining)
                output.append(segment)
                remaining -= len(segment.encode())
                if end < len(data):
                    next_state = {"offset": end, "item_cursor": item_cursor, "digest": digest}
                    break
                if page.next_cursor is None:
                    return ContentPage(
                        availability="available", content="".join(output), source_title=source_title
                    )
                item_cursor = page.next_cursor
                offset = 0
                state = {}
                next_state = {"offset": 0, "item_cursor": item_cursor}
            return ContentPage(
                availability="available",
                content="".join(output),
                source_title=source_title,
                truncated=True,
                next_cursor=self._cursor(scope, identity, next_state),
            )

    async def download_file_source(self, scope, citation_id: str) -> bytes:
        """Download binary attachment through immutable citation authority, never current-file identity."""
        async with self.uow_factory() as uow:
            citation = await uow.execution_content.get_citation(scope, citation_id)
            if citation is None or citation.get("resource_kind") != "file":
                raise ViewNotFound("fixed file citation unavailable")
            await self._source_authority(uow, scope, citation)
            if self.files is None or not all(
                citation.get(key) for key in ("file_id", "content_digest", "object_identity")
            ):
                raise ResourceUnavailable("immutable attachment unavailable")
        return await self.files.read_fixed(
            citation["file_id"], citation["content_digest"], citation["object_identity"], scope
        )
