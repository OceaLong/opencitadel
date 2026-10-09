"""Scoped dataset commands. One caller transaction owns mutations, pins and audit."""

import hashlib
import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.application.evaluation.import_parser import ImportErrorItem, parse_import
from app.domain.evaluation.dataset import (
    MAX_IMPORT_BYTES,
    CaseRevision,
    DatasetDraft,
    DatasetSummary,
    DatasetVersion,
    ImmutableModel,
    validate_case_keys,
    validate_publication,
)
from app.domain.evaluation.errors import (
    CaseResourceUnavailable,
    DatasetConflict,
    DatasetUnavailable,
    ImportInvalid,
)
from app.domain.models.audit_log import AuditLog
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.resource_pin import ResourceUnavailable


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def fingerprint(operation, payload):
    return hashlib.sha256(canonical([operation, payload])).hexdigest()


class ImportPreview(ImmutableModel):
    import_id: UUID
    dataset_id: UUID
    revision: int
    input_digest: str
    errors: tuple[ImportErrorItem, ...]
    added: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    expires_at: datetime


class DatasetVersionSummary(ImmutableModel):
    id: UUID
    dataset_id: UUID
    revision: int
    case_count: int


class DatasetVersionPage(ImmutableModel):
    items: tuple[DatasetVersionSummary, ...]
    next_cursor: str | None = None


class DatasetService:
    def __init__(
        self,
        uow_factory,
        objects,
        object_intents,
        *,
        content=None,
        views=None,
        events=None,
        rule_validator=None,
        cursor_secret=None,
    ):
        self.cursor_secret = cursor_secret
        self.uow_factory, self.objects, self.object_intents = uow_factory, objects, object_intents
        self.content, self.views, self.events, self.rule_validator = (
            content,
            views,
            events,
            rule_validator,
        )

    def _auth(self, scope, principal, request_id=""):
        if request_id and (len(request_id) > 255 or not request_id.strip()):
            raise ValueError("invalid_request_id")
        return AuthorizationContext.for_principal(principal, scope=scope, request_id=request_id)

    async def _begin(self, uow, scope, principal, request_id, operation, payload):
        if not request_id.strip():
            raise ValueError("request_id_required")
        repo = uow.evaluation_dataset
        await repo.authorize(scope, principal, write=True)
        await repo.lock_request(scope, request_id)
        digest = fingerprint(operation, payload)
        return digest, await repo.receipt(scope, request_id, digest)

    async def _finish(self, uow, scope, principal, request_id, digest, operation, result):
        await uow.evaluation_dataset.authorize(scope, principal, write=True)
        serialized = result.model_dump(mode="json", exclude={"cases"})
        if hasattr(result, "cases"):
            serialized["case_revision_ids"] = [str(case.id) for case in result.cases]
        serialized["operation"] = operation
        serialized["audit_resource_id"] = str(
            getattr(result, "dataset_id", getattr(result, "id", ""))
        )
        # Mutation receipt precedes audit so the narrow emitter can verify intent.
        await uow.evaluation_dataset.save_receipt(scope, request_id, digest, serialized)
        log = AuditLog(
            actor_user_id=principal.user_id,
            team_id=scope.team_id,
            action="evaluation.dataset." + operation,
            resource_type="evaluation_dataset",
            resource_id=str(getattr(result, "dataset_id", getattr(result, "id", ""))),
            request_id=request_id,
            metadata={"revision": result.revision},
        )
        await uow.audit.add_evaluation(log, authorization=self._auth(scope, principal, request_id))
        await uow.commit()
        return result

    async def _resume(self, uow, scope, prior, model):
        value = {
            key: item
            for key, item in prior.items()
            if key not in {"operation", "audit_resource_id", "case_revision_ids"}
        }
        if "case_revision_ids" in prior:
            members = await uow.evaluation_dataset.case_members(scope, prior["case_revision_ids"])
            value["cases"] = await self._cases(members)
            await uow.evaluation_dataset.resources(scope, value["cases"])
        return model.model_validate(value)

    @staticmethod
    def _revision(row, expected_revision):
        if row["revision"] != expected_revision:
            raise DatasetConflict("revision_conflict")

    async def _put(self, uow, authorization, dataset_id, cases):
        data = canonical([case.model_dump(mode="json") for case in cases])
        if len(data) > 2 * MAX_IMPORT_BYTES:
            raise ImportInvalid("manifest_limit_exceeded")
        object_id = uuid4()
        await uow.evaluation_dataset.lock_object(object_id)
        digest = hashlib.sha256(data).hexdigest()
        key = await self.object_intents.register(
            authorization, dataset_id=dataset_id, object_id=object_id, digest=digest
        )
        await self.objects.put_bytes(key, data)
        if await self.objects.get_bytes(key) != data:
            raise DatasetUnavailable("object_upload_mismatch")
        return object_id

    async def _manifest(self, row):
        if row.get("cleaned_at") is not None or not row.get("storage_key"):
            raise DatasetUnavailable("case_body_unavailable")
        try:
            data = await self.objects.get_bytes(row["storage_key"])
        except (KeyError, OSError) as error:
            raise DatasetUnavailable("case_body_unavailable") from error
        if len(data) > 2 * MAX_IMPORT_BYTES or hashlib.sha256(data).hexdigest() != row["digest"]:
            raise DatasetUnavailable("case_body_changed")
        try:
            return tuple(CaseRevision.model_validate(value) for value in json.loads(data))
        except (ValueError, TypeError, RecursionError) as error:
            raise DatasetUnavailable("case_body_invalid") from error

    async def _cases(self, members):
        """Bulk fixed-version seam: download/parse each manifest exactly once per call."""
        manifests = {}
        cases = []
        for member in members:
            identity = member["object_id"]
            if identity not in manifests:
                manifests[identity] = await self._manifest(member)
            try:
                case = manifests[identity][member["object_index"]]
            except IndexError as error:
                raise DatasetUnavailable("case_index_invalid") from error
            if (case.id, case.revision, case.case_key) != (
                member["id"],
                member["revision"],
                member["case_key"],
            ):
                raise DatasetUnavailable("case_identity_changed")
            cases.append(case)
        return tuple(cases)

    async def _draft(self, repo, scope, row):
        cases = await self._cases(row["members"])
        await repo.resources(scope, cases)
        return DatasetDraft(
            id=row["id"],
            name=row["name"],
            revision=row["revision"],
            cases=cases,
        )

    async def get_draft(self, scope, principal, dataset_id):
        async with self.uow_factory(self._auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=False)
            return await self._draft(
                uow.evaluation_dataset,
                scope,
                await uow.evaluation_dataset.get_draft(scope, dataset_id),
            )

    async def list_drafts(self, scope, principal):
        async with self.uow_factory(self._auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=False)
            return [
                DatasetSummary(**row) for row in await uow.evaluation_dataset.list_drafts(scope)
            ]

    async def list_versions(self, scope, principal, dataset_id, *, cursor=None, limit=50):
        from app.application.evaluation.discovery_cursor import decode, encode

        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        context = [
            "dataset_versions",
            scope.model_dump(mode="json"),
            principal.user_id,
            str(dataset_id),
        ]
        after = decode(self.cursor_secret, context, cursor) if cursor else None
        if after is not None and (type(after) is not int or after < 1):
            raise ValueError("invalid_cursor")
        async with self.uow_factory(self._auth(scope, principal)) as uow:
            repo = uow.evaluation_dataset
            await repo.authorize(scope, principal, write=False)
            await repo.get_draft(scope, dataset_id)
            rows = await repo.list_versions(scope, dataset_id, before=after, limit=limit + 1)
            return DatasetVersionPage(
                items=tuple(DatasetVersionSummary(**r) for r in rows[:limit]),
                next_cursor=encode(self.cursor_secret, context, rows[limit - 1]["revision"])
                if len(rows) > limit
                else None,
            )

    async def preview_case_from_run(
        self, scope, principal, *, dataset_id, expected_revision, run_id, step_id, at, case_key
    ):
        async with self.uow_factory(self._auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
            self._revision(
                await uow.evaluation_dataset.get_draft(scope, dataset_id), expected_revision
            )
        case = await self.capture_case_from_run(
            scope, run_id=run_id, step_id=step_id, at=at, case_key=case_key
        )
        async with self.uow_factory(self._auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
            self._revision(
                await uow.evaluation_dataset.get_draft(scope, dataset_id), expected_revision
            )
            resources = await uow.evaluation_dataset.resources(scope, (case,))
        return case.model_copy(update={"resources": resources})

    async def get_version(self, scope, principal, version_id):
        async with self.uow_factory(self._auth(scope, principal)) as uow:
            return await self.get_version_in_uow(uow, scope, principal, version_id)

    async def certify_analysis_version(self, scope, principal, version_id):
        """Recover one legacy version after exact object, pin and current resource validation."""
        async with self.uow_factory(self._auth(scope, principal)) as uow:
            version = await self.get_version_in_uow(uow, scope, principal, version_id)
            validate_publication(version.cases, rule_validator=self.rule_validator)
            await uow.evaluation_dataset.certify_analysis(
                scope, principal, version_id, version.pins
            )
            await uow.commit()
        return {"status": "certified"}

    async def get_version_in_uow(self, uow, scope, principal, version_id):
        """Fixed manifest and resource authority read within the caller transaction."""
        repo = uow.evaluation_dataset
        await repo.authorize(scope, principal, write=False)
        row = await repo.get_version(scope, version_id)
        cases = await self._cases(row["members"])
        resources = tuple(
            {
                (r.resource_kind, r.resource_id, r.resource_version): r
                for case in cases
                for r in case.resources
            }.values()
        )
        pins = await uow.resource_pins.validate(
            scope, "dataset_version", str(version_id), resources
        )
        if any(not pin.available for pin in pins):
            raise ResourceUnavailable("dataset_resource_unavailable")
        return DatasetVersion(
            id=row["id"],
            dataset_id=row["dataset_id"],
            revision=row["revision"],
            cases=cases,
            pins=tuple(pin.resource for pin in pins),
        )

    async def create_draft(self, scope, principal, *, request_id, expected_revision, name):
        if expected_revision != 0 or not name.strip() or len(name) > 255:
            raise ValueError("invalid_draft")
        async with self.uow_factory(self._auth(scope, principal, request_id)) as uow:
            digest, prior = await self._begin(
                uow, scope, principal, request_id, "create", [name, expected_revision]
            )
            if prior:
                return await self._resume(uow, scope, prior, DatasetDraft)
            result = DatasetDraft(id=uuid4(), name=name, revision=1)
            await uow.evaluation_dataset.create(scope, result.id, name)
            return await self._finish(uow, scope, principal, request_id, digest, "create", result)

    async def import_validate(
        self, scope, principal, *, dataset_id, request_id, expected_revision, stream, content_type
    ):
        parsed = parse_import(stream, content_type=content_type)
        authorization = self._auth(scope, principal, request_id)
        async with self.uow_factory(authorization) as uow:
            digest, prior = await self._begin(
                uow,
                scope,
                principal,
                request_id,
                "import_validate",
                [str(dataset_id), expected_revision, parsed.digest, content_type],
            )
            if prior:
                return await self._resume(uow, scope, prior, ImportPreview)
            repo = uow.evaluation_dataset
            row = await repo.get_draft(scope, dataset_id)
            await uow.evaluation_archive.require_active(scope, "dataset", dataset_id)
            self._revision(row, expected_revision)
            cases = tuple(
                case.model_copy(update={"revision": expected_revision + 1}) for case in parsed.cases
            )
            errors = list(parsed.errors)
            if not errors:
                fixed_cases = []
                for index, case in enumerate(cases):
                    try:
                        fixed_cases.append(
                            case.model_copy(
                                update={"resources": await repo.resources(scope, (case,))}
                            )
                        )
                    except ResourceUnavailable as error:
                        errors.append(
                            ImportErrorItem(
                                row=parsed.source_rows[index],
                                field=error.field
                                if isinstance(error, CaseResourceUnavailable)
                                else "resources",
                                code="resource_unavailable",
                                message="resource unavailable",
                            )
                        )
                if not errors:
                    cases = tuple(fixed_cases)
            object_id = (
                await self._put(uow, authorization, dataset_id, cases) if not errors else None
            )
            current = await repo.get_draft(scope, dataset_id, lock=True)
            self._revision(current, expected_revision)
            old = {member["case_key"] for member in current["members"]}
            new = {case.case_key for case in cases}
            preview = ImportPreview(
                import_id=uuid4(),
                dataset_id=dataset_id,
                revision=expected_revision,
                input_digest=parsed.digest,
                errors=tuple(errors),
                added=tuple(sorted(new - old)),
                replaced=tuple(sorted(new & old)),
                removed=tuple(sorted(old - new)),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await repo.save_import(
                scope,
                {
                    "id": preview.import_id,
                    "dataset_id": dataset_id,
                    "object_id": object_id,
                    "input_digest": parsed.digest,
                    "draft_revision": expected_revision,
                    "content_type": content_type,
                    "errors": [error.model_dump() for error in errors],
                    "diff": {
                        "added": preview.added,
                        "replaced": preview.replaced,
                        "removed": preview.removed,
                    },
                    "expires_at": preview.expires_at,
                },
            )
            return await self._finish(
                uow, scope, principal, request_id, digest, "import_validate", preview
            )

    async def import_apply(
        self,
        scope,
        principal,
        *,
        dataset_id,
        import_id,
        input_digest,
        request_id,
        expected_revision,
    ):
        async with self.uow_factory(self._auth(scope, principal, request_id)) as uow:
            digest, prior = await self._begin(
                uow,
                scope,
                principal,
                request_id,
                "import_apply",
                [str(dataset_id), str(import_id), input_digest, expected_revision],
            )
            repo = uow.evaluation_dataset
            staging = await repo.get_import(scope, import_id)
            if staging["object_id"] is not None:
                # Serialize expiry cleanup with publication of permanent case references.
                await repo.lock_object(staging["object_id"])
                staging = await repo.get_import(scope, import_id)
            if staging["dataset_id"] != dataset_id or staging["input_digest"] != input_digest:
                raise DatasetConflict("import_identity_conflict")
            if prior:
                # Current resource authorization is still required on idempotent replay.
                await repo.resources(scope, await self._manifest(staging))
                return await self._resume(uow, scope, prior, DatasetDraft)
            await uow.evaluation_archive.require_active(scope, "dataset", dataset_id)
            if staging["errors"] or staging["expires_at"] <= datetime.now(UTC):
                raise ImportInvalid("import_invalid_or_expired")
            cases = await self._manifest(staging)
            row = await repo.get_draft(scope, dataset_id, lock=True)
            self._revision(row, expected_revision)
            staging = await repo.get_import(scope, import_id, lock=True)
            if (
                staging["applied"]
                or staging["draft_revision"] != expected_revision
                or staging["expires_at"] <= datetime.now(UTC)
            ):
                raise DatasetConflict("import_revision_conflict")
            await repo.authorize(scope, principal, write=True)
            await repo.resources(scope, cases)
            validate_case_keys([case.model_dump() for case in cases])
            members = [
                {
                    "id": case.id,
                    "case_key": case.case_key,
                    "revision": case.revision,
                    "object_id": staging["object_id"],
                    "object_index": index,
                }
                for index, case in enumerate(cases)
            ]
            await repo.replace_cases(
                scope, dataset_id, members, expected_revision=expected_revision
            )
            await repo.mark_applied(scope, import_id)
            result = DatasetDraft(
                id=dataset_id, name=row["name"], revision=expected_revision + 1, cases=cases
            )
            return await self._finish(
                uow, scope, principal, request_id, digest, "import_apply", result
            )

    async def update_case(
        self,
        scope,
        principal,
        *,
        dataset_id,
        request_id,
        expected_revision,
        case,
        operation="update_case",
    ):
        if operation not in {"update_case", "from_run"}:
            raise ValueError("invalid_case_operation")
        authorization = self._auth(scope, principal, request_id)
        async with self.uow_factory(authorization) as uow:
            digest, prior = await self._begin(
                uow,
                scope,
                principal,
                request_id,
                operation,
                [
                    str(dataset_id),
                    expected_revision,
                    case.model_dump(mode="json", exclude={"id", "revision"}),
                ],
            )
            repo = uow.evaluation_dataset
            if prior:
                await repo.resources(scope, (case,))
                return await self._resume(uow, scope, prior, DatasetDraft)
            current = await repo.get_draft(scope, dataset_id)
            await uow.evaluation_archive.require_active(scope, "dataset", dataset_id)
            self._revision(current, expected_revision)
            previous_member = next(
                (member for member in current["members"] if member["case_key"] == case.case_key),
                None,
            )
            if previous_member and case.source_run_id is None:
                previous = (await self._cases((previous_member,)))[0]
                if previous.source_run_id is not None:
                    edited = case.input != previous.input or case.history != previous.history
                    provenance = {
                        field: getattr(previous, field)
                        for field in (
                            "source_run_id",
                            "source_step_id",
                            "source_at",
                            "source_content_id",
                            "source_request",
                            "reference_candidate",
                        )
                    }
                    provenance["input_status"] = (
                        "edited" if edited and case.input_confirmed else previous.input_status
                    )
                    case = case.model_copy(update=provenance)
            case = case.model_copy(update={"id": uuid4(), "revision": expected_revision + 1})
            fixed = await repo.resources(scope, (case,))
            case = case.model_copy(update={"resources": fixed})
            object_id = await self._put(uow, authorization, dataset_id, (case,))
            current = await repo.get_draft(scope, dataset_id, lock=True)
            self._revision(current, expected_revision)
            await repo.authorize(scope, principal, write=True)
            await repo.resources(scope, (case,))
            members = [
                member for member in current["members"] if member["case_key"] != case.case_key
            ]
            members.append(
                {
                    "id": case.id,
                    "case_key": case.case_key,
                    "revision": case.revision,
                    "object_id": object_id,
                    "object_index": 0,
                }
            )
            validate_case_keys(members)
            await repo.replace_cases(
                scope, dataset_id, members, expected_revision=expected_revision
            )
            result = await self._draft(repo, scope, await repo.get_draft(scope, dataset_id))
            return await self._finish(uow, scope, principal, request_id, digest, operation, result)

    async def publish(self, scope, principal, *, dataset_id, request_id, expected_revision):
        async with self.uow_factory(self._auth(scope, principal, request_id)) as uow:
            digest, prior = await self._begin(
                uow, scope, principal, request_id, "publish", [str(dataset_id), expected_revision]
            )
            repo = uow.evaluation_dataset
            if prior:
                from app.domain.models.resource_pin import ResourceIdentity

                availability = await uow.resource_pins.validate(
                    scope,
                    "dataset_version",
                    prior["id"],
                    tuple(ResourceIdentity.model_validate(value) for value in prior["pins"]),
                )
                if any(not pin.available for pin in availability):
                    raise ResourceUnavailable("dataset_resource_unavailable")
                return await self._resume(uow, scope, prior, DatasetVersion)
            draft = await repo.get_draft(scope, dataset_id, lock=True)
            self._revision(draft, expected_revision)
            cases = await self._cases(draft["members"])
            validate_publication(cases, rule_validator=self.rule_validator)
            resources = await repo.resources(scope, cases)
            version_id = uuid4()
            row = await repo.publish(
                scope, dataset_id, version_id, expected_revision=expected_revision
            )
            await uow.resource_pins.acquire(scope, "dataset_version", str(version_id), resources)
            await repo.certify_analysis(scope, principal, version_id, resources)
            result = DatasetVersion(
                id=version_id,
                dataset_id=dataset_id,
                revision=row["revision"],
                cases=cases,
                pins=resources,
            )
            return await self._finish(uow, scope, principal, request_id, digest, "publish", result)

    async def _complete_content(self, scope, run_id, step_id, at, kind):
        cursor, pieces, redacted, content_id, seen = None, [], False, None, set()
        size = 0
        while True:
            page = await self.content.read_step_content(
                scope, run_id, step_id, at, cursor, content_kind=kind
            )
            if page.availability != "available" or page.content is None or page.at != at:
                raise DatasetUnavailable(kind + "_unavailable")
            if content_id is not None and page.content_id != content_id:
                raise DatasetUnavailable(kind + "_identity_changed")
            content_id = page.content_id
            size += len(page.content.encode())
            if size > MAX_IMPORT_BYTES:
                raise DatasetUnavailable(kind + "_too_large")
            pieces.append(page.content)
            redacted = redacted or page.redacted
            if not page.truncated:
                if page.next_cursor:
                    raise DatasetUnavailable(kind + "_incomplete")
                return "".join(pieces), redacted, content_id
            if not page.next_cursor or page.next_cursor in seen:
                raise DatasetUnavailable(kind + "_incomplete")
            cursor = page.next_cursor
            seen.add(cursor)

    async def capture_case_from_run(self, scope, *, run_id, step_id, at, case_key):
        if self.content is None or self.views is None or self.events is None:
            raise DatasetUnavailable("from_run_not_configured")
        cut = await self.views.get_step_cut(scope, run_id, step_id, at)
        body, redacted, content_id = await self._complete_content(
            scope, run_id, step_id, cut.at, "input"
        )
        try:
            envelope = json.loads(body)
            context, request = envelope["context"], envelope["request"]
            if not isinstance(context, dict) or not isinstance(request, dict):
                raise TypeError
            message = context["message"]
            history = context.get("conversation", [])
            if not isinstance(message, str) or not message.strip() or not isinstance(history, list):
                raise ValueError
            attachments = tuple(item["file_id"] for item in context.get("attachments", []))
            bindings = tuple(
                {"resource_id": item["resource_id"], "version_id": item["version_id"]}
                for item in context.get("resource_bindings", [])
                if item["resource_kind"] == "knowledge_base"
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DatasetUnavailable("input_unavailable") from error
        # Public persisted message only; never current session or private event history.
        admitted, cursor, seen = None, None, set()
        for _ in range(20):
            page = await self.events.list_events(scope, run_id, after=cursor, limit=500)
            for event in page.events:
                if event.event_type == "message" and isinstance(event.payload.get("message"), str):
                    admitted = event.payload["message"]
                    break
            if admitted is not None or not page.next_cursor:
                break
            if page.next_cursor in seen:
                raise DatasetUnavailable("public_input_incomplete")
            cursor = page.next_cursor
            seen.add(cursor)
        if admitted is None or (not redacted and admitted != message):
            raise DatasetUnavailable("input_message_mismatch")
        candidate = None
        with suppress(DatasetUnavailable):
            candidate, _, _ = await self._complete_content(scope, run_id, step_id, cut.at, "output")
        return CaseRevision(
            case_key=case_key,
            input=message,
            history=history,
            attachments=attachments,
            knowledge_bindings=bindings,
            source_run_id=run_id,
            source_step_id=step_id,
            source_at=cut.at,
            source_content_id=content_id,
            source_request=request,
            input_status="sanitized" if redacted else "admitted",
            input_confirmed=False,
            reference_candidate=candidate,
            reference_answer=None,
            reference_confirmed=False,
        )

    async def create_case_from_run(
        self,
        scope,
        principal,
        *,
        dataset_id,
        request_id,
        expected_revision,
        run_id,
        step_id,
        at,
        case_key,
        attachment_ids=(),
        knowledge_ids=(),
    ):
        # Finish complete F06/F08 reads before entering the dataset write UoW.
        async with self.uow_factory(self._auth(scope, principal, request_id)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
            await uow.evaluation_dataset.get_draft(scope, dataset_id)
        case = await self.capture_case_from_run(
            scope, run_id=run_id, step_id=step_id, at=at, case_key=case_key
        )
        if not set(attachment_ids) <= set(case.attachments) or not set(knowledge_ids) <= {
            b.resource_id for b in case.knowledge_bindings
        }:
            raise ValueError("invalid_source_selection")
        case = case.model_copy(
            update={
                "attachments": tuple(attachment_ids),
                "knowledge_bindings": tuple(
                    b for b in case.knowledge_bindings if b.resource_id in knowledge_ids
                ),
            }
        )
        return await self.update_case(
            scope,
            principal,
            dataset_id=dataset_id,
            request_id=request_id,
            expected_revision=expected_revision,
            case=case,
            operation="from_run",
        )
