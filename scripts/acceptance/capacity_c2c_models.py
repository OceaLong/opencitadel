"""Strict safe C2c wire v1; private bodies/paths are commitments only."""

from typing import Literal

from pydantic import model_validator
from scripts.acceptance.capacity_records import ID, Digest, Nat, Pos, Record

OPERAND_FAMILIES = frozenset(
    (
        "final-input",
        "pinned-input",
        "source-input",
        "batch-results",
        "batch-source",
        "event-source",
        "journal-read",
        "playback-boundary",
        "playback-boundary-observation",
        "playback-checkpoint",
        "playback-missing",
        "playback-observations",
        "playback-orders",
        "playback-run",
        "principal-source",
        "projection",
        "query-rows",
        "resource-source",
        "run-input",
        "signed-configuration",
        "version",
        "version-input",
        "version-source",
        "view-capture",
        "view-coverage",
        "view-generation",
        "view-step-orders",
    )
)
SETTLEMENT_TABLES = (
    "execution_activity_tasks",
    "execution_command_inbox",
    "execution_scheduled_commands",
    "execution_run_projection",
    "execution_model_dispatches",
    "execution_model_settlements",
    "evaluation_budget_reservations",
    "evaluation_budget_buckets",
    "evaluation_execution_leases",
    "evaluation_execution_pools",
    "evaluation_batches",
    "evaluation_environment_leases",
    "evaluation_environment_operations",
    "evaluation_object_intents",
    "execution_poisoned_runs",
    "execution_poisoned_scopes",
    "execution_recovery_requests",
    "execution_view_generations",
    "evaluation_recording_jobs",
    "execution_exports",
    "comparison_sets",
    "knowledge_bases",
    "files",
    "artifact_production_receipts",
    "artifact_upload_intents",
    "artifact_retired_objects",
    "scheduled_jobs",
    "notification_deliveries",
    "patrol_runs",
    "patrol_remediations",
    "execution_usage_delivery",
)
HISTORY_FAMILIES = (
    "object",
    "upload",
    "live_disposition",
    "live_admission",
    "live_window",
    "live_failure",
    "broker_request",
)
PREDICATE_FAMILIES = ("lease", "lease_state", "environment_observation", "environment_read")
SOURCE_FAMILIES = (
    "owners",
    "attempts",
    "judges",
    "versions",
    "objects",
    "runs",
    "cohorts",
    "projectors",
)


class Commitment(Record):
    ordinal: Nat
    sha256: Digest


class Collection(Record):
    count: Nat
    sha256: Digest
    records: list[Commitment]

    @model_validator(mode="after")
    def coverage(self):
        if self.count != len(self.records) or [row.ordinal for row in self.records] != list(
            range(self.count)
        ):
            raise ValueError("safe collection coverage differs")
        return self


class JournalCommitment(Record):
    ordinal: Nat
    identity_sha256: Digest
    body_sha256: Digest
    receipt_sha256: Digest | None


class JournalCollection(Record):
    count: Nat
    sha256: Digest
    records: list[JournalCommitment]

    @model_validator(mode="after")
    def coverage(self):
        if self.count != len(self.records) or [row.ordinal for row in self.records] != list(
            range(self.count)
        ):
            raise ValueError("safe journal coverage differs")
        return self


class OriginalShard(Record):
    ordinal: Nat
    sha256: Digest
    bytes: Pos
    count: Pos
    first: Pos
    last: Pos


class SafeUnit(Record):
    schema_version: Literal[1]
    kind: Literal["base", "round"]
    origin_sha256: Digest
    base_sha256: Digest | None
    manifest_sha256: Digest
    cleanup_sha256: Digest
    final_sha256: Digest
    shards: list[OriginalShard]
    original_records: Nat
    original_nodes: Nat
    families: dict[str, Collection]
    source: dict[str, Collection]
    tables: dict[str, Collection]
    history: dict[str, JournalCollection]
    predicates: dict[str, JournalCollection]
    objects: Collection
    sql: Collection
    transports: Collection
    writers_sha256: Digest
    storage_sha256: Digest
    broker_sha256: Digest
    physical_sha256: Digest
    physical_observations: Collection

    @model_validator(mode="after")
    def complete(self):
        for actual, expected in (
            (self.families, OPERAND_FAMILIES),
            (self.source, SOURCE_FAMILIES),
            (self.tables, (*SETTLEMENT_TABLES, "execution_outbox")),
            (self.history, HISTORY_FAMILIES),
            (self.predicates, PREDICATE_FAMILIES),
        ):
            if set(actual) != set(expected):
                raise ValueError("safe C2c family coverage differs")
        if (self.kind == "base") != (self.base_sha256 is None):
            raise ValueError("safe C2c base relationship differs")
        first = 1
        for ordinal, shard in enumerate(self.shards):
            if (
                shard.ordinal != ordinal
                or shard.first != first
                or shard.last != shard.first + shard.count - 1
            ):
                raise ValueError("safe C2c shard order differs")
            first = shard.last + 1
        if first - 1 != self.original_records:
            raise ValueError("safe C2c shard coverage differs")
        return self


class CompactCollection(Record):
    """Count plus original-value.v1 framed typed commitment, never authority."""

    count: Nat
    sha256: Digest


class OriginalClosureV2(Record):
    encoding: Literal[2]
    raw_files: Pos
    raw_bytes: Pos
    records: Pos
    occurrences: Pos

    @model_validator(mode="after")
    def lifecycle(self):
        if self.records < 2 * self.occurrences:
            raise ValueError("safe original occurrence closure differs")
        return self


class SafeUnitV2(Record):
    schema_version: Literal[2]
    kind: Literal["base", "round"]
    origin_sha256: Digest
    base_sha256: Digest | None
    manifest_sha256: Digest
    cleanup_sha256: Digest
    final_sha256: Digest
    originals: OriginalClosureV2
    families: dict[str, CompactCollection]
    source: dict[str, CompactCollection]
    tables: dict[str, CompactCollection]
    history: dict[str, CompactCollection]
    predicates: dict[str, CompactCollection]
    objects: CompactCollection
    sql: CompactCollection
    transports: CompactCollection
    writers_sha256: Digest
    storage_sha256: Digest
    broker_sha256: Digest
    physical_sha256: Digest
    physical_observations: CompactCollection

    @model_validator(mode="after")
    def complete(self):
        for actual, expected in (
            (self.families, OPERAND_FAMILIES),
            (self.source, SOURCE_FAMILIES),
            (self.tables, (*SETTLEMENT_TABLES, "execution_outbox")),
            (self.history, HISTORY_FAMILIES),
            (self.predicates, PREDICATE_FAMILIES),
        ):
            if set(actual) != set(expected):
                raise ValueError("safe C2c family coverage differs")
        if (self.kind == "base") != (self.base_sha256 is None):
            raise ValueError("safe C2c base relationship differs")
        if self.originals.occurrences != 1 + sum(row.count for row in self.families.values()) + sum(
            row.count for row in (self.objects, self.sql, self.transports)
        ):
            raise ValueError("safe C2c original occurrence count differs")
        return self


class C2c(Record):
    schema_version: Literal[3] = 3
    role: Literal["c2c"] = "c2c"
    attempt_id: ID
    protocol_id: ID
    projection_version: Literal[1, 2]
    units: list[SafeUnit | SafeUnitV2]

    @model_validator(mode="after")
    def unique_units(self):
        origins = [unit.origin_sha256 for unit in self.units]
        if not self.units or len(origins) != len(set(origins)):
            raise ValueError("safe C2c units absent or duplicated")
        if any(unit.schema_version != self.projection_version for unit in self.units):
            raise ValueError("safe C2c projection versions differ")
        return self
