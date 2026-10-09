"""Callable read-only inventory over the actual deployed SQL and version services.

Services and journals are the verified coordinator's existing instances. Import
never opens resources. Errors retain partial identities; no success JSON input.
"""

from uuid import UUID, uuid5

from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity.batch import result_inventory
from scripts.execution_capacity.batch_facts import BatchFacts
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.inventory import (
    SourceInventory,
    batch_membership,
    exact_owned_set,
    make_cohorts,
    read_build_inventory,
    validate_projectors,
)
from scripts.execution_capacity.inventory_readback import (
    SnapshotFacts,
    read_run,
    validate_standard_counts,
)
from scripts.execution_capacity.inventory_sql import (
    ATTEMPTS,
    IDENTITY,
    JUDGES,
    OWNERS,
    PROJECTORS,
    plain,
    read_snapshot,
)
from scripts.seed_execution_visualization import run_identity

from app.domain.analysis.metrics import METRIC_VERSION
from app.domain.models.authorization import AuthorizationMode
from app.domain.models.scope import OwnerScope


class ReadOnlyParents:
    """Existing complete journal union; apparent writes only compare old parents."""

    def __init__(self, journals, *, budget=None, evidence=None):
        self.journals = tuple(journals)
        self.evidence = evidence
        self.budget = budget if budget is not None else EvidenceBudget()

    def _retain_read(self, operation, kind, key, value, error=None):
        if self.evidence is not None:
            self.evidence.retain(
                "journal-read",
                {
                    "sequence": len(self.evidence.originals["journal-read"]) + 1,
                    "operation": operation,
                    "kind": kind,
                    "key": key,
                    "sql_boundary": len(self.evidence.sql_reads),
                    "value": value,
                    "error": error,
                },
            )

    def get(self, kind, key):
        return self.bounded_get(kind, str(key), self.budget)

    def parent(self, kind, key):
        row = self.get(kind, key)
        if row is None:
            raise ValueError("complete original journal parent missing")
        return row["body"]

    def records(self, kind):
        if self.evidence is not None and self.evidence.journal is not None:
            return self._owned_read("records", kind, None, self.budget)
        return list(self.bounded_records(kind, self.budget))

    def bounded_get(self, kind, key, budget):
        key = str(key)
        if self.evidence is not None and self.evidence.journal is not None:
            return self._owned_read("get", kind, key, budget)
        try:
            rows = [j.bounded_get(kind, key, budget) for j in self.journals]
            rows = [r for r in rows if r is not None]
            if rows and any(r != rows[0] for r in rows):
                raise ValueError("conflicting historical journal parent")
            value = rows[0] if rows else None
        except Exception as error:
            self._retain_read("get", kind, key, None, type(error).__name__)
            raise
        self._retain_read("get", kind, key, value)
        return value

    def bounded_records(self, kind, budget):
        if self.evidence is not None and self.evidence.journal is not None:
            yield from self._owned_read("records", kind, None, budget)
            return
        values = {}
        try:
            for journal in self.journals:
                for key, row in journal.bounded_records(kind, budget):
                    if key in values and row != values[key]:
                        raise ValueError("conflicting historical journal parent")
                    values[key] = row
            ordered = sorted(values.items())
        except Exception as error:
            self._retain_read("records", kind, None, None, type(error).__name__)
            raise
        self._retain_read("records", kind, None, ordered)
        yield from ordered

    def _owned_read(self, operation, kind, key, budget):
        from scripts.execution_capacity.history_union import union_rows

        owner = self.evidence.journal
        record = {
            "sequence": len(self.evidence.originals["journal-read"]) + 1,
            "operation": operation,
            "kind": kind,
            "key": key,
            "sql_boundary": len(self.evidence.sql_reads),
        }
        token = owner.begin("operand:journal-read", record)
        try:
            rows = union_rows(
                self.journals, kind, key, owner=owner, parent=token, budget=budget, cumulative=False
            )
            if operation == "get":
                if len(rows) > 1:
                    raise ValueError("duplicate historical parent identity")
                value = None if not rows else rows[0][1]
            else:
                value = rows
        except BaseException as error:
            owner.complete(token, {**record, "value": None, "error": type(error).__name__})
            raise
        owner.complete(token, {**record, "value": value, "error": None})
        return value

    def intent(self, kind, key, body):
        if self.parent(kind, key) != body:
            raise ValueError("actual committed parent differs from original journal parent")


class SourceInventoryReader:
    def __init__(
        self,
        *,
        sessions,
        authorization,
        journals,
        binding,
        seed,
        origin,
        services,
        storage,
        signing_secret,
        source_root,
        build_groups,
        host_fence,
        evidence=None,
    ):
        if binding.get("environment") != "test" or authorization.mode != AuthorizationMode.SYSTEM:
            raise ValueError("verified dedicated test/system inventory authority required")
        self.sessions, self.authorization = sessions, authorization
        self.evidence = evidence
        self.parents = ReadOnlyParents(
            journals,
            budget=None if evidence is None else evidence.budget.child(),
            evidence=evidence,
        )
        self.binding, self.seed, self.origin = binding, seed, origin
        self.services, self.storage, self.signing_secret = services, storage, signing_secret
        self.source_root, self.build_groups, self.host_fence = source_root, build_groups, host_fence

    async def read(self, *, base=()):
        if self.evidence is None:
            return await self._read(base=base)
        with self.evidence.source_inputs(
            binding=self.binding,
            seed=self.seed,
            origin=self.origin,
            build_groups=self.build_groups,
            objects=self.storage.originals,
        ):
            return await self._read(base=base)

    async def _read(self, *, base=()):
        result = SourceInventory()
        owner = original_source_owner(self.evidence)
        outputs = None
        if owner is not None:
            from scripts.execution_capacity.source_outputs import SourceOutputs

            outputs = SourceOutputs(result, owner, parent=self.evidence._cleanup_token)
        stage = "authority"
        try:
            if self.evidence is None:
                raise ValueError("owned private evidence context required")
            await self.host_fence()
            stage = "build"
            result.build = read_build_inventory(
                self.source_root, self.build_groups, budget=self.evidence.budget.child()
            )
            from scripts.execution_capacity.host import source_digest

            actual_source = source_digest(self.source_root, budget=self.evidence.budget.child())
            if (
                actual_source != self.binding["source_sha256"]
                or result.build["digest"] != self.binding["inventory_build_digest"]
            ):
                raise ValueError("actual source/build identity differs from verified deployment")
            result.build["source_digest"] = actual_source
            result.build["metric_version"] = METRIC_VERSION
            async with read_snapshot(self.sessions, self.authorization) as query:
                await collect_source(self, query, result, base, outputs=outputs)
        except Exception as error:  # noqa: BLE001 — retain every read failure; never promote partial facts
            result.errors.append(
                {"stage": stage, "identity": "inventory", "error": type(error).__name__}
            )
            if "query" in locals():
                result.database["reads"] = query.finish_outputs()
        if outputs is not None:
            if "reads" in result.database:
                outputs.close_reads(result.database["reads"])
            outputs.close()
        return result

    async def _run(self, query, run, kind, scope, parent, live_parents, admitted_parents):
        facts = SnapshotFacts(
            query.db,
            self.parents,
            OwnerScope.personal(scope.removeprefix("user:")),
            evidence=self.evidence,
        )
        if kind in {"evaluation_subject", "evaluation_judge"}:
            batch = BatchFacts(
                None,
                None,
                self.parents,
                facts.scope,
                None,
                batch_id=parent,
                evidence=self.evidence,
                parent_kind="live_batch" if parent in live_parents else "batch",
            )
            batch.session = facts.session
            facts.validated_parents[run] = await batch.own_run(run, record=False)
        elif run in admitted_parents:
            facts.validated_parents[run] = admitted_parents[run]
        fact, objects = await read_run(query, facts, run, kind, self.signing_secret, self.storage)
        return fact, objects

    def _base_origin(self, base):
        from scripts.acceptance.capacity_models import SourceOrigin

        if self.origin.kind == "base":
            return self.origin
        from scripts.execution_capacity.cohort_inventory import cohort_control

        base_owner = getattr(base, "owner", None)
        if not base or any(
            control.origin.kind != "base" or control.origin.seal_id != self.origin.seal_id
            for control in (cohort_control(c, owner=base_owner) for c in base)
        ):
            raise ValueError("actual immutable base required for round inventory")
        return SourceOrigin(
            kind="base", seal_id=self.origin.seal_id, round=None, boot_id=None, clone_id=None
        )

    @staticmethod
    def _add(membership, run, value):
        if run in membership:
            raise ValueError("duplicate cohort Run")
        membership[run] = value

    def _counts(self, result, standard, probe_run):
        owner = original_source_owner(self.evidence)
        if owner is None:
            get_run = {r["run_id"]: r for r in result.runs}.__getitem__
        else:
            from scripts.execution_capacity.final_relations import FinalRelations

            relations = FinalRelations(owner, {"source_runs": result.runs})

            def get_run(run_id):
                value = relations.get("source_runs", run_id)
                if value is None:
                    raise KeyError(run_id)
                return value

        for index in range(100000):
            run = str(run_identity(UUID(standard), self.seed, index))
            validate_standard_counts(index, get_run(run))
        probe = get_run(probe_run)
        if (
            probe["status"],
            probe["formal_events"],
            probe["visible_steps"],
        ) != ("completed", 30003, 10000):
            raise ValueError("actual independent step probe differs")

    async def _versions(self, query, result, batch, parent):
        services = self.services[batch["scope_key"]]
        with self.evidence.version_inputs(
            batch=batch,
            parent=parent,
            scope=services.scope,
            principal=services.principal,
            objects=self.storage.originals,
        ):
            dataset = await read_versions(services, self.evidence, result, batch, parent)
        await read_dataset_objects(query, self.storage, result, batch, dataset)


async def read_versions(services, evidence, result, batch, parent):
    scope = services.scope
    principal = services.principal
    suite = await services.suites.get_version(scope, principal, "suite", batch["suite_version"])
    dataset = await services.datasets.get_version(scope, principal, suite.dataset_version)
    rubric = await services.suites.get_version(scope, principal, "rubric", suite.rubric_version)
    evidence.reserve_state(len(suite.config_versions) + 1)
    configs = [
        await services.suites.get_version(scope, principal, "config", x)
        for x in [*suite.config_versions, rubric.judge_config_version]
    ]
    environment = await services.environments.version(scope, principal, suite.environment_version)
    for kind, version in [
        ("suite", suite),
        ("dataset", dataset),
        ("rubric", rubric),
        *[("config", c) for c in configs],
    ]:
        evidence.retain("version", {"kind": kind, "version": version})
        raw = version.model_dump(mode="json")
        result.versions.append(
            {
                "kind": kind,
                "id": str(version.id),
                "batch_id": str(batch["id"]),
                "digest": canonical_digest(raw),
            }
        )
    evidence.retain("version", {"kind": "environment", "version": environment})
    result.versions.append(
        {
            "kind": "environment",
            "id": str(suite.environment_version),
            "batch_id": str(batch["id"]),
            "digest": canonical_digest(plain(environment)),
        }
    )
    actual = await services.batches.get(scope, principal, batch["id"])
    if (
        actual.status
        not in {"completed", "completed_with_errors", "failed", "cancelled", "rejected"}
        or actual.cleanup_status != "clean"
    ):
        raise ValueError("batch source is not terminal/clean")
    matrix = await result_inventory(
        services.batches,
        scope,
        principal,
        batch["id"],
        [c.id for c in dataset.cases],
        suite.config_versions,
        include_identities=True,
        evidence=evidence,
    )
    owner = original_source_owner(evidence)
    if owner is None:
        current = [
            a
            for a in result.attempts
            if a["batch_id"] == str(batch["id"]) and a["attempt"] == a["current_attempt"]
        ]
        exact_owned_set(matrix["result_ids"], {a["result_id"] for a in current})
        exact_owned_set(matrix["run_ids"], {a["run_id"] for a in current})
    else:
        from scripts.execution_capacity.predicate_maps import PredicateMap

        expected_results = PredicateMap(owner, "source_expected")
        expected_runs = PredicateMap(owner, "source_expected")
        for attempt in result.attempts:
            if (
                attempt["batch_id"] == str(batch["id"])
                and attempt["attempt"] == attempt["current_attempt"]
            ):
                expected_results[attempt["result_id"]] = None
                expected_runs[attempt["run_id"]] = None
        exact_owned_set(matrix["result_ids"], expected_results, owner=owner)
        exact_owned_set(matrix["run_ids"], expected_runs, owner=owner)
    result.versions.append(
        {"kind": "results", "id": str(batch["id"]), "digest": matrix["result_digest"]}
    )
    if parent.get("dataset_id") and (
        parent["dataset_id"] != str(dataset.id)
        or parent["config_ids"] != [str(c) for c in suite.config_versions]
        or parent["judge_id"] != str(rubric.judge_config_version)
        or parent["rubric_id"] != str(rubric.id)
    ):
        raise ValueError("published version pins differ from actual original parent")
    return dataset


def original_source_owner(evidence):
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.retained_source import OriginalTrace

    if type(evidence) is EvidenceOwner:
        return evidence.journal
    if type(evidence) is OriginalTrace:
        return evidence.owner
    return None


async def collect_source(reader, query, result, base, *, outputs=None):
    owner = original_source_owner(reader.evidence)
    from scripts.execution_capacity.predicate_maps import PredicateMap

    def mapping(family, pairs=()):
        if owner is None:
            return dict(pairs)
        result = PredicateMap(owner, family)
        for key, value in pairs:
            result[key] = value
        return result

    stage = "database"
    try:
        stage = "database"
        identity = await query.rows("database", IDENTITY)
        if len(identity) != 1:
            raise ValueError("unique database identity missing")
        result.database = plain(identity[0])
        if (
            any(
                result.database[k] != reader.binding[k]
                for k in ("database_name", "database_system_identifier")
            )
            or result.database["migrations"] != [reader.binding["migration"]]
            or result.database["read_only"] != "on"
            or result.database["isolation"] != "repeatable read"
        ):
            raise ValueError("database identity or readonly snapshot differs")
        stage = "owners"
        result.owners = plain(await query.rows("owners", OWNERS))
        if any(r["stream_type"] != "run" for r in result.owners):
            raise ValueError("foreign source stream type")
        stage = "batch-attempts"
        result.attempts = plain(await query.rows("attempts", ATTEMPTS))
        result.judges = plain(await query.rows("judges", JUDGES))
        batch_rows = await query.rows(
            "batches",
            "SELECT id,scope_key,suite_version,status FROM evaluation_batches ORDER BY scope_key,id",
        )
        batches = mapping("source_batches", ((str(r["id"]), r["scope_key"]) for r in batch_rows))
        parents = mapping("source_batch_parents", reader.parents.records("batch"))
        live_parents = mapping("source_live_parents", reader.parents.records("live_batch"))
        if owner is None:
            complete_batches = set(batches) == set(parents) | set(live_parents)
        else:
            complete_batches = all(key in parents or key in live_parents for key in batches)
            complete_batches = all(key in batches for key in parents) and complete_batches
            complete_batches = all(key in batches for key in live_parents) and complete_batches
        if not complete_batches:
            raise ValueError("complete owned batch set differs")
        membership = batch_membership(result.attempts, result.judges, batches, owner=owner)
        origins = mapping("source_origins")
        for row in batch_rows:
            batch = str(row["id"])
            parent = parents.get(batch) or live_parents[batch]
            expected = parent["body"].get("suite_id", parent["body"].get("suite_version"))
            if str(row["suite_version"]) != expected:
                raise ValueError("actual batch pinned suite differs")
            if batch in live_parents and (
                reader.origin.kind != "round"
                or parent["body"].get("window_id") != reader.origin.round.window_id
            ):
                raise ValueError("live batch journal window differs from actual round")
            actual_origin = reader.origin if batch in live_parents else reader._base_origin(base)
            origins[batch] = (
                actual_origin if owner is None else actual_origin.model_dump(mode="json")
            )
            await reader._versions(query, result, row, parent["body"])
        stage = "cohorts"
        standard = reader.binding["fixture_id"]
        scope = "user:" + reader.binding["principal_id"]
        base_origin = reader._base_origin(base)
        origins[standard] = base_origin if owner is None else base_origin.model_dump(mode="json")
        expected = standard_population(standard, reader.seed, reader.evidence)
        actual_values = (
            r["stream_id"]
            for r in result.owners
            if r["source_entity_type"] == "capacity_fixture" and r["source_entity_id"] == standard
        )
        # The original set comprehension collapsed these rows; the later full
        # owner comparison independently rejects duplicate ownership.
        actual = (
            set(actual_values)
            if owner is None
            else mapping("source_actual", ((value, None) for value in actual_values))
        )
        exact_owned_set(actual, expected, owner=owner)
        for run in expected:
            reader._add(membership, run, ("standard", scope, standard))
        probe = reader.binding["probe"]
        pid = probe["fixture_id"]
        origins[pid] = base_origin if owner is None else base_origin.model_dump(mode="json")
        probe_run = probe_run_identity(pid, reader.seed)
        reader._add(membership, probe_run, ("step_capacity", "user:" + probe["principal_id"], pid))
        admitted_parents = mapping("source_admitted")
        for request, row in reader.parents.records("live_admission"):
            if reader.origin.kind != "round" or row["body"]["boot_id"] != reader.origin.boot_id:
                raise ValueError("round admission has foreign/base boot origin")
            found = None
            found_count = 0
            for candidate in result.owners:
                if (
                    candidate["source_entity_type"] == "session"
                    and candidate["source_entity_id"] == row["body"]["session_id"]
                    and candidate["owner_scope_key"] == row["body"]["scope"]
                ):
                    found_count += 1
                    if found is None:
                        found = candidate
            if (
                found_count != 1
                or row["receipt"] is None
                or found["stream_id"] != row["receipt"]["run_id"]
            ):
                raise ValueError("lost/ambiguous admission requires exact source recovery")
            # Existing admission receipt is the actual parent; no Run intent is created.
            run = found["stream_id"]
            parent = reader.origin.round.round_id
            kind = "live" if row["body"]["profile"] == "acceptance-live" else "admission"
            reader._add(membership, run, (kind, row["body"]["scope"], parent))
            origins[parent] = (
                reader.origin if owner is None else reader.origin.model_dump(mode="json")
            )
            admitted_parents[run] = {
                "scope": row["body"]["scope"],
                "request_id": request,
                "parent": row["body"],
            }
        exact_owned_set((r["stream_id"] for r in result.owners), membership, owner=owner)
        for row in result.owners:
            if membership[row["stream_id"]][1] != row["owner_scope_key"]:
                raise ValueError("actual cohort scope differs")
        stage = "projectors"
        result.projectors = plain(await query.rows("projectors", PROJECTORS))
        scopes = (
            {v[1] for v in membership.values()}
            if owner is None
            else mapping("source_scopes", ((value[1], None) for value in membership.values()))
        )
        validate_projectors(result.projectors, scopes, owner=owner)
        for table, predicate in [
            ("execution_poisoned_runs", "TRUE"),
            ("execution_poisoned_scopes", "TRUE"),
            ("execution_recovery_requests", "status NOT IN ('completed')"),
            ("execution_view_generations", "status IN ('building','failed')"),
        ]:
            bad = await query.rows(table, f"SELECT * FROM {table} WHERE {predicate}")
            if bad:
                raise ValueError("unresolved poison/recovery/rebuild source")
        stage = "runs"
        ordered_members = sorted(membership.items()) if owner is None else membership.sorted_items()
        for run, (kind, scope, parent) in ordered_members:
            try:
                fact, objects = await reader._run(
                    query, run, kind, scope, parent, live_parents, admitted_parents
                )
                result.runs.append(fact)
                result.objects.extend(objects)
            except Exception as error:  # noqa: BLE001 — retain every read failure; never promote partial facts
                result.errors.append(
                    {"stage": "run", "identity": run, "error": type(error).__name__}
                )
        result.database["reads"] = query.finish_outputs()
        if outputs is not None:
            outputs.close_reads(result.database["reads"])
            outputs.close_data()
        if not result.errors:
            result.cohorts = make_cohorts(membership, result.runs, origins, outputs=outputs)
            reader._counts(result, standard, probe_run)
            result.reads_complete = True
    except Exception as error:  # noqa: BLE001 - complete original failure prefix
        result.errors.append(
            {"stage": stage, "identity": "inventory", "error": type(error).__name__}
        )
        result.database["reads"] = query.finish_outputs()
        if outputs is not None:
            outputs.close_reads(result.database["reads"])


def standard_population(standard, seed, evidence):
    owner = original_source_owner(evidence)
    if owner is None:
        evidence.reserve_state(100000, bytes_per_item=256)
        return {str(run_identity(UUID(standard), seed, i)) for i in range(100000)}
    from scripts.execution_capacity.predicate_maps import PredicateMap

    values = PredicateMap(owner, "source_expected")
    for ordinal in range(100000):
        values[str(run_identity(UUID(standard), seed, ordinal))] = None
    return values


async def read_dataset_objects(query, storage, result, batch, dataset):
    objects = await query.rows(
        "dataset-objects",
        """SELECT c.id AS case_id,o.id,o.storage_key,o.digest,o.cleaned_at
      FROM evaluation_version_cases v JOIN evaluation_case_revisions c ON c.scope_key=v.scope_key AND c.id=v.case_revision_id
      JOIN evaluation_object_intents o ON o.scope_key=c.scope_key AND o.id=c.object_id
      WHERE v.scope_key=:scope AND v.version_id=:version ORDER BY c.id""",
        {"scope": batch["scope_key"], "version": dataset.id},
    )
    owner = getattr(query, "original_owner", None)
    if owner is None:
        owner = getattr(getattr(query, "trace", None), "owner", None)
    exact_owned_set(
        (str(o["case_id"]) for o in objects), (str(c.id) for c in dataset.cases), owner=owner
    )
    from hashlib import sha256

    for obj in objects:
        if obj["cleaned_at"] is not None:
            raise ValueError("retained case object cleaned")
        data = await storage.get_bytes(obj["storage_key"])
        if sha256(data).hexdigest() != obj["digest"]:
            raise ValueError("retained case object content differs")
        result.objects.append(
            {
                "case_id": str(obj["case_id"]),
                "key": obj["storage_key"],
                "sha256": obj["digest"],
                "size_bytes": len(data),
            }
        )


def probe_run_identity(identity, seed):
    return str(uuid5(UUID(identity), f"visible-step-probe:{seed}"))
