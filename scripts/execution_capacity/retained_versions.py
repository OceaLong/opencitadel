"""Finite original version read ports for the actual evaluation services.

This is a repository port, not a Session/execute/Result emulator. Each method
consumes its actual named original and its exact SQL/UOW observation.
"""

from types import SimpleNamespace

from scripts.execution_capacity.inventory_sql import validate_preflight
from scripts.execution_capacity.retained_run import _literal
from sqlalchemy import text

from app.domain.evaluation.errors import DatasetNotFound
from app.infrastructure.repositories.db_evaluation_batch_repository import (
    DBEvaluationBatchRepository as Batch,
)
from app.infrastructure.repositories.db_evaluation_configuration_repository import table
from app.infrastructure.repositories.db_evaluation_dataset_repository import (
    DBEvaluationDatasetRepository as Dataset,
)
from app.infrastructure.repositories.db_evaluation_dataset_repository import (
    authorize_original_team,
    authorize_original_user,
    params,
)

_FAMILIES = (
    "principal-source",
    "version-source",
    "batch-source",
    "resource-source",
    "version",
    "batch-results",
)


def typed(value):
    if hasattr(type(value), "model_fields"):
        return {name: typed(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, dict):
        return {key: typed(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [typed(item) for item in value]
    return value


class RetainedVersionWork:
    def __init__(self, operands, sql, *, objects, cursor_secret, budget, sql_start=0):
        if not isinstance(cursor_secret, bytes) or len(cursor_secret) < 16:
            raise ValueError("original cursor verification material required")
        self.budget = budget
        self.cursor_secret = cursor_secret
        self.operands = {family: operands[family] for family in _FAMILIES}
        self.positions = dict.fromkeys(_FAMILIES, 0)
        self.sql, self.sql_position, self.sql_start = sql, 0, sql_start
        self.objects, self.object_position = objects, 0
        self.active = None
        self.used_uows = set()
        self.budget.reserve(
            sum(len(v) for v in self.operands.values()) * 16 + len(sql) * 8, rows=len(sql)
        )

    @classmethod
    def from_bundle(
        cls, bundle, operands, sql, objects, *, cursor_secret, budget, family="version-input"
    ):
        from scripts.execution_capacity.evidence_owner import FAMILIES

        if (
            set(bundle) != {"identity", "ranges", "sql", "objects", "error"}
            or bundle["error"] is not None
            or set(bundle["ranges"]) != FAMILIES - {family}
        ):
            raise ValueError("original version bundle incomplete")
        expected_identity = (
            {"batch", "parent", "scope", "principal"}
            if family == "version-input"
            else {"scope", "principal", "id"}
            if family == "pinned-input"
            else set()
        )
        if not expected_identity or set(bundle["identity"]) != expected_identity:
            raise ValueError("original version bundle identity differs")

        def subset(rows, bounds):
            if (
                not isinstance(bounds, list)
                or len(bounds) != 2
                or any(type(n) is not int for n in bounds)
                or not 0 <= bounds[0] <= bounds[1] <= len(rows)
            ):
                raise ValueError("original version bundle bounds differ")
            budget.reserve((bounds[1] - bounds[0]) * 8, rows=bounds[1] - bounds[0])
            return rows[bounds[0] : bounds[1]]

        selected = {
            name: subset(operands[name], bounds) for name, bounds in bundle["ranges"].items()
        }
        if any(rows for name, rows in selected.items() if name not in _FAMILIES):
            raise ValueError("foreign original version bundle operand")
        result = cls(
            selected,
            subset(sql, bundle["sql"]),
            objects=subset(objects, bundle["objects"]),
            cursor_secret=cursor_secret,
            budget=budget,
            sql_start=bundle["sql"][0],
        )
        result.identity = bundle["identity"]
        return result

    def reserve_state(self, items, *, bytes_per_item=256):
        self.budget.reserve(items * bytes_per_item, rows=items)

    def retain(self, family, value):
        if family not in {"version", "batch-results"}:
            raise ValueError("unexpected derived version family")
        index = self.positions[family]
        if index == len(self.operands[family]) or self.operands[family][index] != typed(value):
            raise ValueError("original derived version differs from replay")
        self.positions[family] += 1

    def services(self, scope, principal):
        from app.application.evaluation.batch_service import BatchService
        from app.application.evaluation.dataset_service import DatasetService
        from app.application.evaluation.environment_service import EnvironmentService
        from app.application.evaluation.suite_service import SuiteService

        datasets = DatasetService(self, self, None)
        suites = SuiteService(
            self, datasets, limits=None, policies=None, cursor_secret=self.cursor_secret
        )
        return SimpleNamespace(
            scope=scope,
            principal=principal,
            datasets=datasets,
            suites=suites,
            batches=BatchService(suites, preflight_factory=None),
            environments=EnvironmentService(self, None),
        )

    async def replay(self, result):
        from scripts.execution_capacity.inventory_reader import read_versions

        from app.domain.models.scope import OwnerScope, Principal

        scope = OwnerScope.model_validate(self.identity["scope"])
        principal = Principal.model_validate(self.identity["principal"])
        dataset = await read_versions(
            self.services(scope, principal),
            self,
            result,
            self.identity["batch"],
            self.identity["parent"],
        )
        self.finish()
        return dataset

    def __call__(self, authorization):
        if self.active is not None:
            raise ValueError("overlapping original version UOW")
        if self.sql_position == len(self.sql):
            raise ValueError("original version UOW missing")
        row = self.sql[self.sql_position]
        uow = row["uow"]
        if uow in self.used_uows:
            raise ValueError("original version UOW repeated")
        self.used_uows.add(uow)
        self.active = SimpleNamespace(
            authorization=authorization, uow=uow, snapshot=row["snapshot"]
        )
        return _VersionUow(self)

    def read(self, family, operation, identity, statement, parameters, *, many=False):
        if self.active is None or self.sql_position == len(self.sql):
            raise ValueError("original version SQL missing")
        sql = self.sql[self.sql_position]
        compiled = statement.compile()
        if (
            sql.get("statement") != str(compiled)
            or sql.get("parameters") != parameters
            or sql.get("bound_parameters") != compiled.params
            or sql.get("dispatched") is not True
            or sql.get("error") is not None
            or not 0 < sql["start_ns"] <= sql["end_ns"]
            or (sql["uow"], sql["snapshot"]) != (self.active.uow, self.active.snapshot)
            or sql["snapshot"] != sql["preflight"]["snapshot"]
        ):
            raise ValueError("original version SQL/UOW differs")
        validate_preflight(sql["preflight"], self.budget)
        self.budget.reserve(
            sql["preflight"]["total_bytes"] * 64 + sql["preflight"]["row_count"] * 4096,
            rows=sql["preflight"]["total_bytes"] + sql["preflight"]["row_count"] * 16,
        )
        reference = {
            "sql_index": self.sql_start + self.sql_position,
            "uow": sql["uow"],
            "snapshot": sql["snapshot"],
        }
        values = []
        rows = self.operands[family]
        while self.positions[family] < len(rows):
            row = rows[self.positions[family]]
            if row.get("read") != reference:
                break
            if (
                set(row) != {"read", "operation", "identity", "value"}
                or row["operation"] != operation
                or row["identity"] != typed(identity)
            ):
                raise ValueError("original version operation/identity differs")
            self.positions[family] += 1
            values.append(row["value"])
            if not many:
                break
        if not values or (
            not many
            and self.positions[family] < len(rows)
            and rows[self.positions[family]].get("read") == reference
        ):
            raise ValueError("original version explicit operand coverage differs")
        self.sql_position += 1
        if not many:
            expected = len(values[0]) if operation == "batch.counts" else int(values[0] is not None)
            if expected != sql["preflight"]["row_count"]:
                raise ValueError("original version scalar cardinality differs")
            return values[0]
        if values == [[]]:
            if sql["preflight"]["row_count"] != 0:
                raise ValueError("original explicit empty row count differs")
            return []
        if len(values) != sql["preflight"]["row_count"]:
            raise ValueError("original version row cardinality differs")
        if any(not isinstance(value, dict) or not value for value in values):
            raise ValueError("original version row/empty coverage differs")
        return values

    async def get_bytes(self, key):
        if self.object_position == len(self.objects):
            raise ValueError("original version object read missing")
        row = self.objects[self.object_position]
        self.object_position += 1
        if (
            row["key"] != key
            or row["error"] is not None
            or not 0 < row["start_ns"] <= row["end_ns"]
            or type(row["data"]) is not bytes
        ):
            raise ValueError("original version object identity/read differs")
        self.budget.reserve(len(row["data"]) * 64, rows=1)
        return row["data"]

    def finish(self):
        if (
            self.active is not None
            or self.sql_position != len(self.sql)
            or self.object_position != len(self.objects)
            or any(self.positions[k] != len(v) for k, v in self.operands.items())
        ):
            raise ValueError("unconsumed original version inputs")


class _VersionUow:
    def __init__(self, owner):
        self.owner = owner
        self.evaluation_dataset = _Dataset(owner)
        self.evaluation_configuration = _Configuration(owner)
        self.evaluation_batch = _Batch(owner)
        self.evaluation_environment = _Environment(owner)
        self.resource_pins = _Pins(owner)

    async def __aenter__(self):
        return self

    async def __aexit__(self, kind, value, traceback):
        owner = self.owner
        if (
            kind is None
            and owner.sql_position < len(owner.sql)
            and owner.sql[owner.sql_position]["uow"] == owner.active.uow
        ):
            raise ValueError("incomplete original version UOW consumption")
        owner.active = None


class _Dataset:
    def __init__(self, owner):
        self.owner = owner

    async def authorize(self, scope, principal, *, write):
        auth = self.owner.active.authorization
        if auth.scope != scope or auth.principal != principal or write:
            raise ValueError("original readonly version authorization differs")
        user = self.owner.read(
            "principal-source",
            "dataset.authorize",
            {"scope": scope, "principal": principal, "write": write},
            _literal(Dataset.authorize, "SELECT status,token_version"),
            {"id": principal.user_id},
        )
        authorize_original_user(scope, principal, write=write, user=user)
        if scope.team_id:
            role = self.owner.read(
                "principal-source",
                "dataset.team_role",
                {"scope": scope, "principal": principal},
                _literal(Dataset.authorize, "SELECT role FROM team_members"),
                {"team": scope.team_id, "user": principal.user_id},
            )
            authorize_original_team(scope, principal, role)

    async def get_version(self, scope, version_id):
        row = self.owner.read(
            "version-source",
            "dataset.get_version",
            {"scope": scope, "id": version_id},
            _literal(Dataset.get_version, "SELECT id,dataset_id,revision"),
            params(scope, id=version_id),
        )
        if not row:
            raise DatasetNotFound("version_unavailable")
        members = self.owner.read(
            "version-source",
            "dataset.members",
            {"scope": scope, "dataset": row["dataset_id"], "version": version_id},
            text(
                "SELECT c.*,o.storage_key,o.digest,o.cleaned_at FROM evaluation_version_cases m JOIN evaluation_case_revisions c ON c.scope_key=m.scope_key AND c.id=m.case_revision_id JOIN evaluation_object_intents o ON o.scope_key=c.scope_key AND o.id=c.object_id WHERE m.scope_key=:scope AND m.version_id=:version ORDER BY m.case_key"
            ),
            params(scope, dataset=row["dataset_id"], version=version_id),
            many=True,
        )
        return {**row, "members": members}


class _Configuration:
    def __init__(self, owner):
        self.owner = owner

    async def get_version(self, scope, kind, version_id):
        body = self.owner.read(
            "version-source",
            "configuration.get_version",
            {"scope": scope, "kind": kind, "id": version_id},
            text(f"SELECT body FROM {table(kind)} WHERE scope_key=:scope AND id=:id"),
            params(scope, id=version_id),
        )
        if body is None:
            raise DatasetNotFound("configuration_version_unavailable")
        return body


class _Batch:
    def __init__(self, owner):
        self.owner = owner

    async def get(self, scope, batch_id):
        row = self.owner.read(
            "batch-source",
            "batch.get",
            {"scope": scope, "id": batch_id},
            _literal(Batch.get, "SELECT * FROM evaluation_batches"),
            params(scope, id=batch_id),
        )
        if row is None:
            raise DatasetNotFound("batch_unavailable")
        return dict(row)

    async def counts(self, scope, batch_id):
        rows = self.owner.read(
            "batch-source",
            "batch.counts",
            {"scope": scope, "id": batch_id},
            _literal(Batch.counts, "SELECT execution_status,count(*)"),
            params(scope, id=batch_id),
        )
        if any(set(row) != {"execution_status", "count"} for row in rows):
            raise ValueError("original batch count columns differ")
        return {row["execution_status"]: row["count"] for row in rows}

    async def results(self, scope, batch_id, *, after=-1, limit=100):
        return self.owner.read(
            "batch-source",
            "batch.results",
            {"scope": scope, "id": batch_id, "after": after, "limit": limit},
            _literal(Batch.results, "SELECT r.*,a.run_id"),
            params(scope, id=batch_id, after=after, limit=limit),
            many=True,
        )


class _Environment:
    def __init__(self, owner):
        self.owner = owner

    async def registered(self, scope, kind, identity, revision=None, *, current=True):
        from app.infrastructure.repositories.db_evaluation_environment_repository import (
            registered_original,
        )

        statement = text(
            "SELECT revision,body,digest FROM evaluation_environment_registry WHERE scope_key=:scope AND kind=:kind AND id=:id"
            + (" AND revision=:revision" if not current and revision is not None else "")
            + " ORDER BY revision DESC LIMIT 1"
        )
        row = self.owner.read(
            "version-source",
            "environment.registered",
            {
                "scope": scope,
                "kind": kind,
                "id": identity,
                "revision": revision,
                "current": current,
            },
            statement,
            params(scope, kind=kind, id=identity, revision=revision),
        )
        return registered_original(row, kind, revision, current=current)


class _Pins:
    def __init__(self, owner):
        self.owner = owner

    async def validate(self, scope, owner_kind, owner_id, resources):
        from app.infrastructure.repositories.db_resource_pin_repository import (
            DBResourcePinRepository,
            scope_params,
            validate_original_pin,
        )

        self.owner.budget.reserve(len(resources) * 256, rows=len(resources))
        output = []
        for resource in resources:
            row = self.owner.read(
                "resource-source",
                "pins.validate",
                {
                    "scope": scope,
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "resource": resource,
                },
                _literal(DBResourcePinRepository.validate, "SELECT available,unavailable_reason"),
                {
                    **scope_params(scope),
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "kind": resource.resource_kind,
                    "resource": resource.resource_id,
                    "version": resource.resource_version,
                },
            )
            row = _row(row, {"available", "unavailable_reason"})
            output.append(await validate_original_pin(scope, resource, row, self))
        return output

    async def resolve(self, scope, resource, *, lock=False):
        from app.infrastructure.repositories.db_resource_pin_repository import (
            _SCOPE,
            artifact_version_number,
            require_resolved_resource,
            require_resource_owner,
            scope_params,
        )

        if lock:
            raise ValueError("retained version resource lock forbidden")
        p = {
            **scope_params(scope),
            "id": resource.resource_id,
            "version": resource.resource_version,
        }
        if resource.resource_kind == "knowledge_base":
            value = self.owner.read(
                "resource-source",
                "pins.knowledge_owner",
                p,
                text(
                    f"SELECT id FROM knowledge_bases WHERE id=:id AND {_SCOPE} AND deleted_at IS NULL"
                ),
                p,
            )
            require_resource_owner(value)
            statement = text(
                "SELECT id FROM knowledge_base_versions WHERE knowledge_base_id=:id AND id=:version AND published_at IS NOT NULL AND state IN ('ready','degraded')"
            )
            columns = {"id"}
            bound = p
        elif resource.resource_kind == "artifact":
            session = self.owner.read(
                "resource-source",
                "pins.artifact_session",
                p,
                text("SELECT session_id FROM artifacts WHERE id=:id"),
                p,
            )
            require_resource_owner(session)
            bound = {**p, "session": session}
            owned = self.owner.read(
                "resource-source",
                "pins.session_owner",
                bound,
                text(
                    f"SELECT id FROM sessions WHERE id=:session AND {_SCOPE} AND deleted_at IS NULL"
                ),
                bound,
            )
            require_resource_owner(owned)
            number = artifact_version_number(resource.resource_version)
            statement = text(
                "SELECT version_refs->>(:number-1) AS storage_key FROM artifacts WHERE id=:id AND session_id=:session AND jsonb_array_length(version_refs)>=:number"
            )
            bound = {**bound, "number": number}
            columns = {"storage_key"}
        elif resource.resource_kind == "file":
            statement = text(
                f"SELECT key,content_digest FROM files WHERE id=:id AND {_SCOPE} AND content_digest=:version AND content_available"
            )
            columns = {"key", "content_digest"}
            bound = p
        else:
            statement = text(
                f"SELECT content_id,citation_refs FROM execution_public_content WHERE content_id::text=:id AND {_SCOPE} AND content_digest=:version AND EXISTS (SELECT 1 FROM execution_content_bindings b WHERE b.content_id=execution_public_content.content_id AND b.scope_key=execution_public_content.scope_key)"
            )
            columns = {"content_id", "citation_refs"}
            bound = p
        raw = self.owner.read(
            "resource-source",
            "pins.resolve",
            {"scope": scope, "resource": resource, "lock": lock},
            statement,
            bound,
        )
        row = _row(raw, columns)
        return await require_resolved_resource(
            scope,
            resource,
            row,
            file_repository=_File(self.owner),
            knowledge_repository=_Knowledge(self.owner),
        )


def _row(value, columns):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != columns:
        raise ValueError("original resource columns differ")
    return SimpleNamespace(**value)


def _resource_model(kind, raw):
    from sqlalchemy import inspect

    from app.infrastructure.models.file import FileModel
    from app.infrastructure.models.knowledge_base import KnowledgeBaseModel, KnowledgeDocumentModel

    if raw is None:
        return None
    if kind not in {FileModel, KnowledgeBaseModel, KnowledgeDocumentModel} or set(raw) != {
        c.key for c in inspect(kind).column_attrs
    }:
        raise ValueError("original resource model coverage differs")
    return kind(**raw)


class _File:
    def __init__(self, owner):
        self.owner = owner

    async def get_by_id(self, file_id, scope=None):
        from sqlalchemy import select

        from app.infrastructure.models.file import FileModel
        from app.infrastructure.repositories.db_file_repository import DBFileRepository

        statement = DBFileRepository(None)._apply_scope(
            select(FileModel).where(FileModel.id == file_id), scope
        )
        raw = self.owner.read(
            "resource-source",
            "db_file_repository.py",
            {"scope": scope, "id": file_id},
            statement,
            {},
        )
        model = _resource_model(FileModel, raw)
        return model.to_domain() if model is not None else None


class _Knowledge:
    def __init__(self, owner):
        self.owner = owner

    async def get_kb(self, kb_id, scope=None):
        from sqlalchemy import select

        from app.infrastructure.models.knowledge_base import KnowledgeBaseModel
        from app.infrastructure.repositories.db_knowledge_base_repository import (
            DBKnowledgeBaseRepository,
        )

        repo = DBKnowledgeBaseRepository(None)
        statement = repo._exclude_deleted(
            repo._apply_scope(
                select(KnowledgeBaseModel).where(KnowledgeBaseModel.id == kb_id), scope
            )
        )
        raw = self.owner.read(
            "resource-source",
            "db_knowledge_base_repository.py",
            {"scope": scope, "id": kb_id},
            statement,
            {},
        )
        model = _resource_model(KnowledgeBaseModel, raw)
        return model.to_domain() if model is not None else None

    async def get_document_for_version(self, kb_id, version_id, doc_id):
        from app.infrastructure.models.knowledge_base import KnowledgeDocumentModel
        from app.infrastructure.repositories.kb.retrieval_mixin import document_version_statement

        row = self.owner.read(
            "resource-source",
            "knowledge.document_version",
            {"kb_id": kb_id, "version_id": version_id, "doc_id": doc_id},
            document_version_statement(kb_id, version_id, doc_id),
            {},
        )
        if row is None:
            return None
        if set(row) != {"KnowledgeDocumentModel", "document_revision_id"}:
            raise ValueError("original citation document columns differ")
        return _resource_model(
            KnowledgeDocumentModel, row["KnowledgeDocumentModel"]
        ).to_domain(), str(row["document_revision_id"])
