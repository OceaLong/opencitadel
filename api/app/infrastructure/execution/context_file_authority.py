"""Immutable accepted attachment representations with current file authority."""

import hashlib
from uuid import uuid5

from sqlalchemy import select

from app.application.execution.context_objects import ContextObjectStore
from app.application.services.agent_service import _private_attachment
from app.domain.execution.context_media import inline_image_part
from app.domain.execution.context_models import (
    ContextMessage,
    ContextSource,
    ContextSourceRef,
    context_digest,
)
from app.domain.execution.context_resources import ContextFileEvidence
from app.domain.external.object_storage import ObjectNotFoundError
from app.infrastructure.execution.context_skill_authority import freeze_context_skill
from app.infrastructure.models.file import FileModel


async def _current_file(session, scope, file_id):
    owner = (
        FileModel.team_id == scope.team_id
        if scope.team_id
        else ((FileModel.owner_user_id == scope.user_id) & FileModel.team_id.is_(None))
    )
    row = await session.scalar(
        select(FileModel).where(FileModel.id == file_id, owner).with_for_update(read=True)
    )
    if (
        row is None
        or not row.content_available
        or not row.content_digest
        or not row.object_identity
    ):
        raise ValueError("CONTEXT_RESOURCE_AUTHORIZATION_REVOKED")
    return row.to_domain()


class ContextInputResourceResolver:
    def __init__(self, objects):
        self.objects = objects

    async def __call__(self, *, work, scope, payload, run_id):
        result = await freeze_context_skill(work=work, scope=scope, payload=payload)
        result.pop("_context_files", None)
        attachments = result.get("attachments", [])
        if not isinstance(attachments, list) or len(attachments) > 10:
            raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
        if not attachments:
            return result
        if work is None:
            raise ValueError("CONTEXT_ADMISSION_TRANSACTION_REQUIRED")
        total, frozen, selected = 0, [], []
        for raw in attachments:
            if not isinstance(raw, dict) or not isinstance(raw.get("file_id"), str):
                raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")  # noqa: TRY004 - typed admission failure
            file = await _current_file(work.db_session, scope, raw["file_id"])
            if file.id in {item["file_id"] for item in selected}:
                raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
            total += file.size
            if file.size < 0 or total > 8388608:
                raise ValueError("CONTEXT_ATTACHMENT_BYTES_EXCEEDED")
            try:
                body = await self.objects._storage.get_bounded_bytes(file.key, max(1, file.size))
            except ObjectNotFoundError as error:
                raise ValueError("CONTEXT_SOURCE_UNAVAILABLE") from error
            if (
                body.truncated
                or len(body.data) != file.size
                or hashlib.sha256(body.data).hexdigest() != file.content_digest
            ):
                raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID")
            attachment = _private_attachment(file)
            selected.append(attachment)
            identity = uuid5(run_id, "context-attachment:" + file.id)
            representation, ref = "manifest", None
            if file.mime_type in {"image/png", "image/jpeg"}:
                representation = "image"
                ref = await self.objects.put_context_document(
                    identity,
                    {
                        "kind": "context_attachment",
                        "file_id": file.id,
                        "media": inline_image_part(body.data, file.mime_type),
                    },
                    run_id=run_id,
                    scope_key=f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}",
                )
            elif file.mime_type.startswith("text/") or file.mime_type == "application/json":
                try:
                    content = body.data.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError("CONTEXT_ATTACHMENT_ENCODING_UNSUPPORTED") from error
                representation = "text"
                ref, _ = await ContextObjectStore(self.objects._storage).put_result(
                    run_id=run_id,
                    activity_id=identity,
                    scope_key=f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}",
                    payload={"message": {"role": "user", "content": content}},
                )
            elif file.mime_type.startswith(("image/", "audio/", "video/")):
                raise ValueError("CONTEXT_ATTACHMENT_MEDIA_UNSUPPORTED")
            frozen.append(
                ContextFileEvidence(
                    file_id=file.id,
                    content_digest=file.content_digest,
                    object_identity=file.object_identity,
                    filename=file.filename,
                    mime_type=file.mime_type,
                    size=file.size,
                    sandbox_path=attachment["sandbox_path"],
                    representation=representation,
                    object_ref=ref,
                ).model_dump(mode="json")
            )
        result.update(attachments=selected, _context_files=frozen)
        return result


async def validate_file_version(session, scope, evidence):
    file = await _current_file(session, scope, evidence.file_id)
    if (
        file.content_digest != evidence.content_digest
        or str(file.object_identity) != str(evidence.object_identity)
        or file.filename != evidence.filename
        or file.mime_type != evidence.mime_type
        or file.size != evidence.size
        or _private_attachment(file)["sandbox_path"] != evidence.sandbox_path
    ):
        raise ValueError("CONTEXT_RESOURCE_VERSION_CHANGED")
    return file


async def validate_context_files(session, scope, payload):
    raw = payload.get("_context_files", [])
    attachments = payload.get("attachments", [])
    if not isinstance(raw, list) or len(raw) > 10 or not isinstance(attachments, list):
        raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
    evidence = tuple(ContextFileEvidence.model_validate(item) for item in raw)
    if len({item.file_id for item in evidence}) != len(evidence) or [
        item.file_id for item in evidence
    ] != [item.get("file_id") for item in attachments]:
        raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
    for item in sorted(evidence, key=lambda item: item.file_id):
        file = await validate_file_version(session, scope, item)
        if _private_attachment(file) != next(
            raw for raw in attachments if raw["file_id"] == item.file_id
        ):
            raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
    return evidence


async def attachment_source(objects, evidence, *, run_id, scope_key, epoch):
    from app.domain.execution.context_media import validate_inline_image

    text = (
        f"Attachment: {evidence.filename}; path: {evidence.sandbox_path}; bytes: {evidence.size}."
    )
    media = ()
    if evidence.representation == "image":
        if not evidence.object_ref.startswith(
            f"execution/results/{uuid5(run_id, 'context-attachment:' + evidence.file_id)}/"
        ):
            raise ValueError("CONTEXT_ATTACHMENT_REFERENCE_INVALID")
        document = await objects.load_context_document(evidence.object_ref)
        data = validate_inline_image(document.get("media"))
        if (
            document.get("kind") != "context_attachment"
            or document.get("file_id") != evidence.file_id
            or hashlib.sha256(data).hexdigest() != evidence.content_digest
        ):
            raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID")
        media = (document["media"],)
    elif evidence.representation == "text":
        index = await attachment_index(objects, evidence, run_id=run_id, scope_key=scope_key)

        async def permit(_index):
            return None  # Caller holds current file/principal locks in this transaction.

        data, _, _ = await read_attachment_span(
            objects, index, offset=0, limit=2048, before_read=permit
        )
        text += "\nOriginal text excerpt:\n" + data
    message = ContextMessage(role="user", content=text, media_parts=media)
    return ContextSource(
        ref=ContextSourceRef(
            source_id=f"attachment:{run_id}:{evidence.file_id}",
            scope_key=scope_key,
            run_id=run_id,
            kind="attachment",
            digest=context_digest(evidence.model_dump(mode="json")),
            formal_position=0,
            authorization_epoch=epoch,
        ),
        protected=True,
        messages=(message,),
        messages_digest=context_digest([message.model_dump(mode="json")]),
    )


async def attachment_index(objects, evidence, *, run_id, scope_key):
    index = await ContextObjectStore(objects._storage).load_index(
        ref=evidence.object_ref,
        digest=evidence.object_ref.rsplit("/", 1)[-1].removesuffix(".json"),
        scope_key=scope_key,
        run_id=run_id,
    )
    if (
        index.activity_id != uuid5(run_id, "context-attachment:" + evidence.file_id)
        or index.content_digest != evidence.content_digest
        or index.content_bytes != evidence.size
    ):
        raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID")
    return index


async def read_attachment_span(objects, index, *, offset, limit, before_read):
    data, following = await ContextObjectStore(objects._storage).read_span(
        index, offset=offset, limit=limit, before_read=before_read
    )
    return data.decode("utf-8"), following, index.content_bytes
