"""Actual after-exit source/DB/Minio/broker readback assembly for C2c4.

Callers supply verified readonly observer resources, not a caller-authored clean
JSON. Observers must live outside the writer containers stopped by this round.
"""

import asyncio
import time
from uuid import UUID

from scripts.execution_capacity.batch_facts import BatchFacts
from scripts.execution_capacity.broker_inventory import (
    expected_requests,
    read_broker,
    reconcile,
    request_parents,
)
from scripts.execution_capacity.cumulative_cleanup import SettlementReader
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.inventory_reader import ReadOnlyParents, SourceInventoryReader
from scripts.execution_capacity.physical import PhysicalJournalSnapshot, verify_physical_clean
from scripts.execution_capacity.storage_inventory import MinioInventory

from app.domain.models.scope import OwnerScope


class CumulativeJournal(ReadOnlyParents):
    def __init__(self, journals, output, *, budget=None, evidence=None):
        super().__init__((*journals, output), budget=budget, evidence=evidence)
        self.output = output

    def intent(self, kind, key, body, *, body_owner=None):
        if body_owner is not None:
            if body_owner.index_bytes is None:
                raise ValueError("explicit native original index quota required")
            self.output.budget = self.budget
            if self.output.index_bytes is None:
                self.output.index_bytes = body_owner.index_bytes
        prior = self.get(kind, key)
        if prior is not None:
            if self.evidence is not None and self.evidence.journal is not None:
                from scripts.execution_capacity.replay_relations import same_value

                equal = same_value(
                    prior["body"], body, owner=self.evidence.journal, budget=self.budget
                )
            else:
                equal = prior["body"] == body
            if not equal:
                raise ValueError("historical parent changed during final observation")
        if body_owner is None:
            self.output.intent(kind, key, body)
        else:
            self.output.intent(kind, key, body, body_owner=body_owner)
        if prior is not None and prior["receipt"] is not None:
            self.output.acknowledge(kind, key, prior["receipt"])

    def native_view(self, body):
        from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal, RecoveryJournal

        matches = []
        for journal in self.journals:
            if type(journal) in (RecoveryJournal, ReadOnlyRecoveryJournal):
                view = journal.native_view(body)
                if view is not None:
                    matches.append(view)
        if len(matches) > 1:
            raise ValueError("ambiguous native original body owner")
        return None if not matches else matches[0]

    def acknowledge(self, kind, key, receipt):
        self.intent(kind, key, self.parent(kind, key))
        self.output.acknowledge(kind, key, receipt)

    def get(self, kind, key):
        return self.bounded_get(kind, key, self.budget)

    def bounded_get(self, kind, key, budget):
        if self.evidence is not None and self.evidence.journal is not None:
            rows = self._owned_union(kind, str(key), budget)
            if len(rows) > 1:
                raise ValueError("duplicate cumulative parent identity")
            return None if not rows else rows[0][1]
        values = [
            r
            for journal in self.journals
            if (r := journal.bounded_get(kind, key, budget)) is not None
        ]
        return join_history(values)

    def records(self, kind):
        if self.evidence is not None and self.evidence.journal is not None:
            return self._owned_union(kind, None, self.budget)
        return list(self.bounded_records(kind, self.budget))

    def bounded_records(self, kind, budget):
        if self.evidence is not None and self.evidence.journal is not None:
            yield from self._owned_union(kind, None, budget)
            return
        values = {}
        for journal in self.journals:
            for key, row in journal.bounded_records(kind, budget):
                values[key] = join_history([values[key], row]) if key in values else row
        yield from sorted(values.items())

    def _owned_union(self, kind, key, budget):
        from scripts.execution_capacity.history_union import union_rows

        if self.evidence._cleanup_token is None:
            raise ValueError("durable cleanup history owner required before acquisition")
        return union_rows(
            self.journals,
            kind,
            key,
            owner=self.evidence.journal,
            parent=self.evidence._cleanup_token,
            budget=budget,
            cumulative=True,
        )


def join_history(values):
    if not values:
        return None
    if any(row["body"] != values[0]["body"] for row in values):
        raise ValueError("conflicting cumulative intent")
    receipts = [r["receipt"] for r in values if r["receipt"] is not None]
    if receipts and any(r != receipts[0] for r in receipts):
        raise ValueError("conflicting cumulative receipt")
    return {"body": values[0]["body"], "receipt": receipts[0] if receipts else None}


class FinalInventory:
    def __init__(self, *, writers, reader, journal, storage, binding, docker, base=()):
        self.writers, self.reader, self.journal = writers, reader, journal
        self.storage, self.binding, self.docker, self.base = storage, binding, docker, base
        self._round_originals = None
        self.budget = journal.budget if journal is not None else EvidenceBudget()

    @classmethod
    def from_resources(
        cls,
        *,
        writers,
        resources,
        authorization,
        journals,
        output_journal,
        binding,
        seed,
        origin,
        services,
        signing_secret,
        source_root,
        build_groups,
        host_fence,
        docker,
        base=(),
    ):
        """Construct concrete reader from separate observer clients and full journals."""
        from scripts.execution_capacity.evidence_owner import EvidenceOwner
        from scripts.execution_capacity.observer_session import ObserverSession

        evidence = resources.evidence
        sessions = resources.postgres.session_factory
        options = getattr(sessions, "kw", None)
        if (
            type(evidence) is not EvidenceOwner
            or evidence.journal is None
            or evidence._cleanup_token is None
            or not isinstance(options, dict)
            or options.get("sync_session_class") is not ObserverSession
            or options.get("budget") is not evidence.budget
            or options.get("evidence") is not evidence
        ):
            raise ValueError("original-backed observer source required for final acquisition")
        journal = CumulativeJournal(
            journals, output_journal, budget=evidence.budget.child(), evidence=evidence
        )
        reader = SourceInventoryReader(
            sessions=sessions,
            authorization=authorization,
            journals=(journal,),
            binding=binding,
            seed=seed,
            origin=origin,
            services=services,
            storage=resources.evidence_objects,
            evidence=evidence,
            signing_secret=signing_secret,
            source_root=source_root,
            build_groups=build_groups,
            host_fence=host_fence,
        )
        return cls(
            writers=writers,
            reader=reader,
            journal=journal,
            storage=resources.object_storage_client,
            binding=binding,
            docker=resources.evidence_transport,
            base=base,
        )

    async def read(self):
        with self.reader.evidence.final_inputs(
            binding=self.binding,
            bucket=self.storage.bucket,
            origin=self.reader.origin,
            base=self.base,
            objects=self.reader.storage.originals,
        ):
            return await self._read()

    async def _read(self):
        result = {
            "start_ns": time.monotonic_ns(),
            "complete": False,
            "issues": [],
            "counts": {},
            "reads": {},
        }
        original_owner = self.reader.evidence.journal
        issue_writer = (
            None
            if original_owner is None
            else original_owner.begin_collection(
                self.reader.evidence._cleanup_token, "final-issues"
            )
        )

        def issue(value):
            if issue_writer is None:
                result["issues"].append(value)
            else:
                issue_writer.append(value)

        def issues(values):
            for value in values:
                issue(value)

        # This independent physical/journal check cannot be substituted by the
        # earlier supervisor report or a caller-provided empty issue array.
        result["writers"] = self.writers.final_journals()
        issues(result["writers"]["issues"])

        async def observe(name, action):
            before = time.monotonic_ns()
            try:
                value = await action()
                result[name] = value
                return value
            except Exception as error:  # noqa: BLE001 - retain failed acquisition and continue safe cleanup
                issue({"kind": name, "state": "error", "type": type(error).__name__})
                return None
            finally:
                result["reads"][name] = {"start_ns": before, "end_ns": time.monotonic_ns()}

        base_cohorts = inherited_cohorts(
            self.base, self.reader.origin, budget=self.budget, owner=original_owner
        )
        source = await observe("source", lambda: self.reader.read(base=base_cohorts))
        if source is not None:
            if not source.reads_complete or source.errors:
                issue({"kind": "source", "state": "error"})
            settlement = await observe(
                "settlement",
                lambda: SettlementReader(
                    self.reader.sessions, self.reader.authorization, source
                ).read(),
            )
            if settlement is not None:
                issues(settlement["issues"])
                result["counts"]["database"] = settlement["counts"]
                retained = retained_history(
                    self.journal, result["writers"]["uploads"], settlement["rows"], source
                )
                result["retained_history"] = retained
                issues(retained["issues"])
                # Actual per-batch historical attempt/slot/generation joins,
                # including all operations and all claims, bypass clean caches.
                for batch in settlement["rows"].get("evaluation_batches", ()):

                    async def read_leases(batch=batch):
                        facts = BatchFacts(
                            self.reader.sessions,
                            self.reader.authorization,
                            self.journal,
                            OwnerScope.personal(batch["scope_key"].removeprefix("user:")),
                            None,
                            batch_id=UUID(batch["id"]),
                            evidence=self.reader.evidence,
                            parent_kind="batch"
                            if self.journal.get("batch", batch["id"]) is not None
                            else "live_batch",
                        )
                        leases, clean = await facts.environments(final=True)
                        if not clean:
                            raise ValueError("cumulative batch leases unresolved")
                        return [row.model_dump(mode="json") for row in leases]

                    await observe("leases:" + batch["id"], read_leases)
        storage = await observe(
            "storage",
            lambda: asyncio.to_thread(
                lambda: MinioInventory(
                    self.storage.client, self.storage.bucket, budget=self.budget
                ).read()
            ),
        )
        if storage is not None:
            result["counts"]["storage"] = {
                "objects": len(storage.objects),
                "uploads": len(storage.uploads),
                "pages": len(storage.pages),
            }
            try:
                expected = {}
                if source is None:
                    raise ValueError("source object authority missing")
                source.require_complete()
                for row in source.objects:
                    value = {"size": row["size_bytes"], "sha256": row["sha256"]}
                    if row["key"] in expected and expected[row["key"]] != value:
                        raise ValueError("conflicting owned object identity")
                    expected[row["key"]] = value
                storage.require_quiescent(expected)
            except ValueError as error:
                issue({"kind": "objects", "state": "error", "type": type(error).__name__})
        broker = await observe(
            "broker",
            lambda: asyncio.to_thread(read_broker, self.binding, self.docker, budget=self.budget),
        )
        if broker is not None:
            result["counts"]["broker"] = {
                "operations": len(broker.operations),
                "bindings": len(broker.bindings),
                "pages": len(broker.pages),
            }
            try:
                originals = request_parents(expected_requests(self.journal), self.journal)
                versions = {}
                for record in originals.operations.values():
                    body = record["request"]
                    scope = body["lease"]["case_slot"]["workspace"]
                    identity = body["lease"]["environment_version"]
                    services = self.reader.services[scope]
                    if (scope, identity) not in versions:
                        with self.reader.evidence.pinned_inputs(
                            scope=services.scope,
                            principal=services.principal,
                            identity=UUID(identity),
                            objects=self.reader.storage.originals,
                        ):
                            actual = await services.environments.version(
                                services.scope, services.principal, UUID(identity)
                            )
                        versions[(scope, identity)] = actual.model_dump(mode="json")
                    if versions[(scope, identity)] != body["version"]:
                        raise ValueError("original broker request full pinned version differs")
                result["pinned_environment_versions"] = [
                    {"scope": key[0], "id": key[1], "version": value}
                    for key, value in sorted(versions.items())
                ]
                issues(reconcile(broker, originals))
                result["physical_observations"] = []
                result["physical"] = await asyncio.to_thread(
                    verify_physical_clean,
                    self.binding,
                    PhysicalJournalSnapshot.capture(self.journal),
                    self.docker,
                    broker_inventory=broker,
                    observations=result["physical_observations"],
                )
            except Exception as error:  # noqa: BLE001 - retain failed acquisition and continue safe cleanup
                issue({"kind": "physical", "state": "error", "type": type(error).__name__})
        if self.journal is not None:
            result["predicate_journals"] = {}
            try:
                for kind in ("lease", "lease_state", "environment_observation", "environment_read"):
                    from scripts.execution_capacity.history_union import family_records

                    result["predicate_journals"][kind] = family_records(self.journal, kind)
            except Exception as error:  # noqa: BLE001 - preserve completed families and failed quota
                issue(
                    {"kind": "predicate-journals", "state": "error", "type": type(error).__name__}
                )
        try:
            from contextlib import ExitStack

            from scripts.execution_capacity.predicate_history import validate_predicate_history

            with ExitStack() as inherited_owner:
                base_final = base_owner = None
                if self.reader.origin.kind == "round":
                    from scripts.execution_capacity.round_originals import OwnedRoundOriginals

                    if (
                        type(self._round_originals) is not OwnedRoundOriginals
                        or self._round_originals.final is not self
                    ):
                        raise ValueError(
                            "actual original round owner required for inherited history"
                        )
                    base_evidence = inherited_owner.enter_context(
                        self._round_originals.verified_base_evidence()
                    )
                    base_final = base_evidence.roots["cleanup"]["quiescence"]["final"]
                    base_owner = getattr(base_evidence.view, "journal", None)
                if all(name in result for name in ("source", "settlement", "predicate_journals")):
                    validate_predicate_history(
                        result,
                        budget=self.budget,
                        sql=self.reader.evidence.sql_reads,
                        base=base_final,
                        native_journal=self.journal,
                        base_owner=base_owner,
                    )
        except Exception as error:  # noqa: BLE001 - retain exact incomplete final prefix
            issue({"kind": "predicate-history", "state": "error", "type": type(error).__name__})
        if issue_writer is not None:
            result["issues"] = issue_writer.complete()
        result["complete"] = (
            all(name in result for name in ("source", "settlement", "storage", "broker"))
            and not result["issues"]
        )
        result["end_ns"] = time.monotonic_ns()
        return result


def retained_history(journal, sdk_uploads, rows, inventory, *, expected_issues=None):
    """Join evidence layers; the SDK count alone counts physical PUT attempts."""
    from scripts.execution_capacity.history_union import family_records

    retained = {
        kind: family_records(journal, kind)
        for kind in (
            "object",
            "upload",
            "live_disposition",
            "live_admission",
            "live_window",
            "live_failure",
            "broker_request",
        )
    }
    from scripts.execution_capacity.final_relations import FinalRelations
    from scripts.execution_capacity.replay_relations import same_value
    from scripts.execution_capacity.retained_final import RetainedHistory

    owner = (
        journal.owner
        if type(journal) is RetainedHistory
        else (
            journal.evidence.journal
            if type(journal) is CumulativeJournal and journal.evidence is not None
            else None
        )
    )
    issues = []
    issue_writer = None
    issue_count = 0
    if owner is not None:
        if expected_issues is not None:
            from scripts.execution_capacity.original_collections import (
                BaseCollectionRows,
                CollectionRows,
                PlainBaseCollectionRows,
                PlainCollectionRows,
            )

            if (
                type(expected_issues)
                not in (
                    CollectionRows,
                    PlainCollectionRows,
                    BaseCollectionRows,
                    PlainBaseCollectionRows,
                )
                or expected_issues.owner is not owner
            ):
                raise ValueError("actual original retained issues required")
            owner._collection_metadata(expected_issues)
        elif type(journal) is CumulativeJournal:
            issue_writer = owner.begin_collection(
                journal.evidence._cleanup_token, "retained-history-issues"
            )
        else:
            raise ValueError("original retained issues required for replay")

    def fail(kind, key):
        nonlocal issue_count
        value = {"kind": kind, "identity": key, "state": "error"}
        if expected_issues is not None and owner is not None:
            if issue_count >= len(expected_issues) or not same_value(
                value, expected_issues[issue_count], owner=owner, budget=owner.budget
            ):
                raise ValueError("original retained issue differs")
        elif issue_writer is not None:
            issue_writer.append(value)
        else:
            issues.append(value)
        issue_count += 1

    relations = None
    if owner is not None:
        relations = FinalRelations(
            owner,
            {
                "objects": inventory.objects,
                "linked": sdk_uploads,
                "owners": inventory.owners,
                "dispatches": rows.get("execution_model_dispatches", ()),
                "reservations": rows.get("evaluation_budget_reservations", ()),
                "settlements": rows.get("execution_model_settlements", ()),
                "outcomes": rows.get("execution_run_projection", ()),
            },
        )
        objects = linked = owners = dispatches = reservations = settlements = outcomes = None
    else:
        objects = {row["key"]: row for row in inventory.objects}
        linked = {}
        owners = {str(r["stream_id"]): r["owner_scope_key"] for r in inventory.owners}
        dispatches = {
            (r["scope_key"], str(r["call_identity"])): r
            for r in rows.get("execution_model_dispatches", ())
        }
        reservations = {
            (r["scope_key"], str(r["call_identity"])): r
            for r in rows.get("evaluation_budget_reservations", ())
        }
        settlements = {
            (r["scope_key"], str(r["call_identity"])): r
            for r in rows.get("execution_model_settlements", ())
        }
        outcomes = {str(r["run_id"]): r["status"] for r in rows.get("execution_run_projection", ())}
    for key, record in sdk_uploads.items():
        body = record["body"]
        port = body.get("port_upload_id")
        if port is not None:
            if relations is None:
                linked.setdefault(port, []).append(body)
            if port not in retained["upload"]:
                fail("missing-port-upload", key)
    for key, record in retained["upload"].items():
        body = record["body"]
        physical = linked.get(key, []) if relations is None else relations.all("linked", key)
        count, first = 0, None
        for candidate in physical:
            count += 1
            if first is None:
                first = candidate
        if (
            record["receipt"] is None
            or count != 1
            or any(first.get(k) != body.get(k) for k in ("key", "size", "sha256"))
        ):
            fail("port-upload", key)
    for key, record in retained["object"].items():
        body = record["body"]
        actual = objects.get(key) if relations is None else relations.get("objects", key)
        if (
            record["receipt"] is None
            or actual is None
            or actual["size_bytes"] != body["size"]
            or actual["sha256"] != body["sha256"]
        ):
            fail("port-object", key)
    for key, record in retained["live_disposition"].items():
        body = record["body"]
        if owner is not None:
            # Both structures belong to a single already row-bounded original
            # disposition. Database matches below have no such bound.
            size = len(body["runs"]) * 128 + len(body["dispatches"]) * 256
            owner.budget.reserve(size, rows=0, largest=size)
        runs = set(body["runs"])
        actual = {} if relations is None else None
        for identity, dispatch in (
            dispatches.items() if relations is None else relations.items("dispatches")
        ):
            run = str(dispatch["run_id"])
            if run not in runs:
                continue
            if (owners.get(run) if relations is None else relations.get("owners", run)) != identity[
                0
            ]:
                fail("live-owner", key)
            if relations is None:
                reservation, settlement = (
                    reservations.get(identity, {}),
                    settlements.get(identity, {}),
                )
                actual[identity[1]] = {
                    "run_id": run,
                    "call_identity": identity[1],
                    "state": reservation.get("state"),
                    "settlement": reservation.get("settlement"),
                    "fact": settlement.get("fact"),
                }
        original = {r["call_identity"]: r for r in body["dispatches"]}
        if relations is None:
            actual_equal = original == actual
        else:
            count, actual_equal = 0, True
            for call, (identity, dispatch) in relations.calls(runs):
                reservation = relations.get("reservations", identity, {})
                settlement = relations.get("settlements", identity, {})
                value = {
                    "run_id": str(dispatch["run_id"]),
                    "call_identity": call,
                    "state": reservation.get("state"),
                    "settlement": reservation.get("settlement"),
                    "fact": settlement.get("fact"),
                }
                if original.get(call) != value:
                    actual_equal = False
                count += 1
            actual_equal = actual_equal and count == len(original)
        if (
            len(original) != len(body["dispatches"])
            or not actual_equal
            or set(body["outcomes"]) != runs
            or any(
                (outcomes.get(run) if relations is None else relations.get("outcomes", run))
                != status
                for run, status in body["outcomes"].items()
            )
        ):
            fail("live-disposition", key)
    if retained["live_admission"] and not retained["live_disposition"]:
        fail("live-disposition", "missing")
    if owner is not None and expected_issues is not None:
        if issue_count != len(expected_issues):
            raise ValueError("original retained issue count differs")
        issues = expected_issues
    elif issue_writer is not None:
        issues = issue_writer.complete()
    return {
        "records": retained,
        "issues": issues,
        "counts": {kind: len(values) for kind, values in retained.items()},
        "physical_sdk_uploads": len(sdk_uploads),
    }


def inherited_cohorts(base, origin, *, budget, owner=None):
    from scripts.acceptance.capacity_models import Cohort

    if origin.kind == "base":
        if base:
            raise ValueError("base stage cannot inherit another base")
        return []
    if len(base) != 1:
        raise ValueError("round requires exact full immutable base")
    source = base[0]
    cohorts = source["cohorts"] if isinstance(source, dict) else source.cohorts
    if owner is not None:
        from scripts.execution_capacity.cohort_inventory import cohort_control

        owner._collection_metadata(cohorts)
        if not cohorts:
            raise ValueError("round inherited cohort origin differs")
        for row in cohorts:
            control = cohort_control(row, owner=owner)
            if control.origin.kind != "base" or control.origin.seal_id != origin.seal_id:
                raise ValueError("round inherited cohort origin differs")
        return cohorts
    budget.reserve(len(cohorts) * 512, rows=len(cohorts))
    result = [
        Cohort.model_validate(row.model_dump() if type(row) is Cohort else row) for row in cohorts
    ]
    if not result or any(
        row.origin.kind != "base" or row.origin.seal_id != origin.seal_id for row in result
    ):
        raise ValueError("round inherited cohort origin differs")
    return result
