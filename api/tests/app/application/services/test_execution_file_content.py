import asyncio
import hashlib
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.domain.external.file_storage import FileUploadPayload
from tests.app.application.services.test_artifact_provenance_postgres import seed
from tests.app.execution_test_support import execution_admin_session

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("postgres_integration")]


async def test_real_upload_persists_digest_and_pinned_delete_precedes_object_io():
    from app.application.services.file_service import FileService
    from app.domain.models.resource_pin import ResourceIdentity, ResourcePinned, ResourceUnavailable
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.external.file_storage.minio_file_storage import MinioFileStorage
    from app.infrastructure.repositories.db_file_repository import DBFileRepository
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository

    owner, session_id = await seed()
    scope = OwnerScope.personal(owner)

    @asynccontextmanager
    async def uow():
        async with execution_admin_session() as db:
            yield SimpleNamespace(
                file=DBFileRepository(db),
                resource_pins=DBResourcePinRepository(db),
                commit=db.commit,
            )

    # Transport-only object stand-in; actual adapter, producer, SQL repository,
    # transaction, scope, digest validation and pin guards execute unmocked.
    class Client:
        def __init__(self):
            self.data = {}
            self.deletes = []

        def put_object(self, **kw):
            self.data[kw["object_name"]] = kw["data"].read()

        def get_object(self, bucket, key):
            return BytesIO(self.data[key])

        def remove_object(self, bucket, key):
            self.deletes.append(key)
            self.data.pop(key, None)

    client = Client()
    storage = MinioFileStorage("bucket", SimpleNamespace(client=client), uow)
    body = "immutable附件🙂".encode()
    file = await storage.upload_file(
        FileUploadPayload(
            file=BytesIO(body), filename="test.txt", size=len(body), owner_user_id=owner
        )
    )
    assert getattr(file, "content_digest", None) == hashlib.sha256(body).hexdigest(), (
        "upload did not persist verified digest"
    )
    assert file.object_identity
    delete_started = asyncio.Event()
    delete_pid = []

    async def concurrent_delete():
        async with execution_admin_session() as db:
            delete_pid.append(await db.scalar(text("SELECT pg_backend_pid()")))
            delete_started.set()
            await DBFileRepository(db).prepare_delete(file.id, scope)

    async with uow() as unit:
        saved = await unit.file.get_by_id(file.id, scope)
        assert saved.content_digest == file.content_digest
        assert saved.object_identity == file.object_identity
        ref = ResourceIdentity(
            resource_kind="file", resource_id=file.id, resource_version=file.content_digest
        )
        await unit.resource_pins.acquire(scope, "session", session_id, [ref])
        blocker = await unit.resource_pins.db_session.scalar(text("SELECT pg_backend_pid()"))
        deleting = asyncio.create_task(concurrent_delete())
        try:
            await delete_started.wait()
            async with execution_admin_session() as observer:
                async with asyncio.timeout(5):
                    while not await observer.scalar(  # noqa: ASYNC110 - observe actual database lock
                        text("SELECT :blocker=ANY(pg_blocking_pids(:pid))"),
                        {"blocker": blocker, "pid": delete_pid[0]},
                    ):
                        await asyncio.sleep(0.01)
            await unit.commit()
            with pytest.raises(ResourcePinned):
                await asyncio.wait_for(deleting, 5)
        finally:
            if not deleting.done():
                deleting.cancel()
    service = FileService(uow, storage)
    assert (
        await service.read_fixed(file.id, file.content_digest, str(file.object_identity), scope)
        == body
    )
    client.data[file.key] = b"drift"
    with pytest.raises(ResourceUnavailable):
        await service.read_fixed(file.id, file.content_digest, str(file.object_identity), scope)
    client.data[file.key] = body
    with pytest.raises(ResourcePinned):
        await service.delete_file(file.id, scope)
    assert client.deletes == []
    await service.delete_file(file.id, scope, force=True)
    assert client.deletes == [file.key]
    async with uow() as unit:
        results = await unit.resource_pins.validate(scope, "session", session_id, [ref])
        assert not results[0].available
        assert results[0].reason == "force_deleted"


async def test_tombstone_object_failure_remains_unavailable_and_authorized_retry_cleans():
    from app.application.services.file_service import FileService
    from app.domain.errors import NotFoundError
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.external.file_storage.minio_file_storage import MinioFileStorage
    from app.infrastructure.repositories.db_file_repository import DBFileRepository

    owner, _ = await seed()
    scope = OwnerScope.personal(owner)

    @asynccontextmanager
    async def uow():
        async with execution_admin_session() as db:
            yield SimpleNamespace(file=DBFileRepository(db), commit=db.commit)

    class Client:
        def __init__(self):
            self.data = {}
            self.fail = True

        def put_object(self, **kw):
            self.data[kw["object_name"]] = kw["data"].read()

        def remove_object(self, bucket, key):
            if self.fail:
                raise OSError("storage unavailable")
            self.data.pop(key, None)

    client = Client()
    storage = MinioFileStorage("bucket", SimpleNamespace(client=client), uow)
    file = await storage.upload_file(
        FileUploadPayload(file=BytesIO(b"bytes"), filename="a.txt", size=5, owner_user_id=owner)
    )
    service = FileService(uow, storage)
    with pytest.raises(OSError, match="storage unavailable"):
        await service.delete_file(file.id, scope)
    with pytest.raises(NotFoundError):
        await service.get_file_info(file.id, scope)
    assert file.key in client.data
    client.fail = False
    await service.delete_file(file.id, scope)
    assert file.key not in client.data
    async with uow() as unit:
        assert await unit.file.get_by_id(file.id, scope) is None
