"""Complete original lease history joins, shared by acquisition and offline replay."""

import json
from hashlib import sha256
from uuid import UUID

from scripts.acceptance.capacity_c2c_models import PREDICATE_FAMILIES
from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity.batch_facts import (
    ATTEMPT_SQL,
    LEASE_SQL,
    OPERATION_SQL,
    lease_observation_clean,
)
from scripts.execution_capacity.inventory_sql import (
    IDENTITY,
    parameter_types,
    plain,
    validate_preflight,
)

from app.domain.evaluation.environment import EnvironmentLease


def validate_predicate_history(
    final, *, budget, sql, base=None, native_journal=None, base_owner=None
):
    from scripts.execution_capacity.evidence_json import equal_streams
    from scripts.execution_capacity.original_journal import OriginalJournal, _Occurrences
    from scripts.execution_capacity.original_plain import canonical_original_digest, canonical_parts

    if base_owner is not None:
        if base is None or type(base_owner) is not OriginalJournal:
            raise ValueError("actual inherited original owner required")
        base_owner._usable()

    if native_journal is not None:
        from scripts.execution_capacity.final_inventory import CumulativeJournal
        from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal, RecoveryJournal

        if type(native_journal) not in (
            CumulativeJournal,
            RecoveryJournal,
            ReadOnlyRecoveryJournal,
        ):
            raise ValueError("concrete native original journal required")
    original_owner = None
    if type(sql) is _Occurrences:
        if type(sql.owner) is not OriginalJournal or not sql.owner.owns_sequence(sql, "sql"):
            raise ValueError("exact original SQL owner required")
        sql.owner._usable()
        original_owner = sql.owner

    def same_history(left, right):
        if original_owner is None:
            return left == right
        from scripts.execution_capacity.predicate_maps import PredicateMap
        from scripts.execution_capacity.replay_relations import same_value

        if type(right) is PredicateMap:
            return right.same_values(left)
        return same_value(left, right, owner=original_owner, budget=budget)

    def mapping(family, pairs=()):
        if original_owner is None:
            return dict(pairs)
        from scripts.execution_capacity.predicate_maps import PredicateMap

        result = PredicateMap(original_owner, family)
        for key, value in pairs:
            result[key] = value
        return result

    records = final["predicate_journals"]
    if set(records) != set(PREDICATE_FAMILIES):
        raise ValueError("complete original lease history families required")
    current = mapping(
        "current",
        ((str(row["id"]), row) for row in final["settlement"]["rows"]["evaluation_batches"]),
    )
    source = final["source"] if isinstance(final["source"], dict) else vars(final["source"])
    source_relations = None
    if original_owner is not None:
        from scripts.execution_capacity.final_relations import FinalRelations

        source_relations = FinalRelations(original_owner, {"source_attempts": source["attempts"]})
    actual_leases = mapping(
        "actual_leases",
        (
            (str(row["id"]), row)
            for row in final["settlement"]["rows"]["evaluation_environment_leases"]
        ),
    )
    if len(actual_leases) != len(final["settlement"]["rows"]["evaluation_environment_leases"]):
        raise ValueError("duplicate final lease identity")
    inherited = {} if base is None else base["predicate_journals"]
    previous_batches = (
        {}
        if base is None
        else mapping(
            "previous_batches",
            ((str(row["id"]), row) for row in base["settlement"]["rows"]["evaluation_batches"]),
        )
    )
    states = mapping("states")
    observations = mapping("observations")
    leases = mapping("leases")
    final_reads = mapping("final_reads")
    seen_current = set() if original_owner is None else mapping("seen_current")
    for key, record in records["environment_read"].items():
        budget.reserve(512, rows=1)
        body = record["body"]
        view = None if native_journal is None else native_journal.native_view(body)
        owner = original_owner if view is None else view.journal

        def digest(value, *, owner=owner):
            return canonical_original_digest(value, owner=owner, budget=budget)

        def equal(left, right, *, owner=owner, left_owner=None):
            return equal_streams(
                canonical_parts(
                    left, owner=owner if left_owner is None else left_owner, budget=budget
                ),
                canonical_parts(right, owner=owner, budget=budget),
            )

        if (
            set(record) != {"body", "receipt"}
            or record["receipt"] is not None
            or digest(body) != key
            or set(body)
            != {
                "batch_id",
                "scope",
                "final",
                "start_ns",
                "end_ns",
                "reads",
                "leases",
                "attempts",
                "operations",
                "error",
                "database",
                "query_observations",
            }
            or type(body["final"]) is not bool
            or body["error"] is not None
            or not 0 < body["start_ns"] <= body["end_ns"]
        ):
            raise ValueError("original lease history read incomplete")
        batch = body["batch_id"]
        parent = current.get(batch)
        if parent is None:
            parent = previous_batches.get(batch)
            inherited_record = inherited.get("environment_read", {}).get(key)
            if parent is None or not equal(inherited_record, record, left_owner=base_owner):
                raise ValueError("orphan original historical batch read")
        if body["scope"] != parent["scope_key"]:
            raise ValueError("foreign original historical batch scope")
        rows = body["leases"]
        if original_owner is None:
            ids = [str(row["id"]) for row in rows]
            complete_ids = (
                len(ids) == len(set(ids))
                and set(body["attempts"]) == set(ids)
                and set(body["operations"]) == set(ids)
            )
        else:
            ids = mapping("lease_ids", ((str(row["id"]), None) for row in rows))
            complete_ids = (
                len(ids) == len(rows)
                and ids.same_keys(body["attempts"])
                and ids.same_keys(body["operations"])
            )
        if not complete_ids:
            raise ValueError("original history lease/attempt/operation coverage differs")
        reads = iter(body["reads"])
        observed = iter(body["query_observations"])
        snapshot = None
        last = body["start_ns"]
        read_id = None
        ordinal = 0
        uow = None

        def read(name, sql, params, values, *, reads=reads, body=body, observed=observed):
            nonlocal snapshot, last, read_id, ordinal, uow
            receipt = next(reads, None)
            if receipt is None or set(receipt) != {
                "name",
                "sql_digest",
                "parameter_digest",
                "parameters",
                "start_ns",
                "end_ns",
                "preflight",
                "rows",
                "result_digest",
            }:
                raise ValueError("original historical query receipt incomplete")
            if (
                receipt["name"] != name
                or receipt["sql_digest"] != canonical_digest(sql)
                or receipt["parameters"] != plain(params)
                or receipt["parameter_digest"] != canonical_digest(plain(params))
                or receipt["rows"] != len(values)
                or receipt["result_digest"] != digest(plain(values))
                or not last <= receipt["start_ns"] <= receipt["end_ns"] <= body["end_ns"]
            ):
                raise ValueError("original historical query operands differ")
            observation = next(observed, None)
            if (
                observation is None
                or set(observation)
                != {
                    "read_id",
                    "ordinal",
                    "owner",
                    "name",
                    "statement",
                    "parameters",
                    "parameter_types",
                    "rows",
                    "read",
                    "sql_index",
                    "dispatch",
                }
                or observation["ordinal"] != ordinal
                or observation["name"] != name
                or observation["statement"] != sql
                or observation["parameters"] != plain(params)
                or observation["parameter_types"] != parameter_types(params)
                or not equal(observation["rows"], plain(values))
                or observation["read"] != receipt
            ):
                raise ValueError("historical original query observation differs")
            UUID(observation["read_id"])
            if read_id is not None and observation["read_id"] != read_id:
                raise ValueError("historical query snapshot owner differs")
            read_id = observation["read_id"]
            ordinal += 1
            dispatch = observation["dispatch"]
            if observation["owner"] == "observer":
                from sqlalchemy import text

                if (
                    type(observation["sql_index"]) is not int
                    or observation["sql_index"] < 0
                    or type(dispatch) is not dict
                    or dispatch.get("error") is not None
                    or dispatch.get("dispatched") is not True
                    or dispatch["statement"] != sql
                    or dispatch["parameters"] != plain(params)
                    or dispatch["bound_parameters"] != text(sql).compile().params
                    or dispatch["preflight"] != receipt["preflight"]
                    or dispatch["snapshot"] != receipt["preflight"]["snapshot"]
                    or not receipt["start_ns"]
                    <= dispatch["start_ns"]
                    <= dispatch["end_ns"]
                    <= receipt["end_ns"]
                ):
                    raise ValueError("historical original SQL dispatch differs")
                if uow is not None and uow != dispatch["uow"]:
                    raise ValueError("historical original UOW differs")
                uow = dispatch["uow"]
            elif (
                observation["owner"] != "readonly-snapshot"
                or dispatch is not None
                or observation["sql_index"] is not None
            ):
                raise ValueError("historical original readonly owner absent")
            preflight = receipt["preflight"]
            validate_preflight(preflight, budget)
            budget.reserve(
                preflight["total_bytes"] * 64 + preflight["row_count"] * 4096,
                rows=preflight["total_bytes"] + preflight["row_count"] * 16,
            )
            if preflight["row_count"] != len(values) or (
                snapshot is not None and preflight["snapshot"] != snapshot
            ):
                raise ValueError("original historical snapshot/cardinality differs")
            snapshot = preflight["snapshot"]
            last = receipt["end_ns"]

        read("database", IDENTITY, {}, body["database"])
        if len(body["database"]) != 1 or any(
            body["database"][0][name] != source["database"][name]
            for name in ("database_name", "database_system_identifier", "migrations")
        ):
            raise ValueError("historical lease database differs")
        if len(body["reads"]) < 2:
            raise ValueError("historical lease enumeration absent")
        params = body["reads"][1]["parameters"]
        if (
            set(params) != {"scope", "batch", "final", "clean"}
            or type(params["clean"]) is not list
            or len(set(params["clean"])) != len(params["clean"])
        ):
            raise ValueError("historical lease cache differs")
        for identity in params["clean"]:
            UUID(identity)
        read(
            "leases",
            LEASE_SQL,
            {
                "scope": body["scope"],
                "batch": batch,
                "final": body["final"],
                "clean": [UUID(value) for value in params["clean"]],
            },
            rows,
        )
        in_current = body["start_ns"] >= final["start_ns"]
        if in_current:
            if not body["query_observations"]:
                raise ValueError("current lease read lacks actual owner SQL linkage")
            start = body["query_observations"][0]["sql_index"]
            if (
                type(start) is not int
                or start < 0
                or start + len(body["query_observations"]) > len(sql)
            ):
                raise ValueError("current lease read lacks actual owner SQL linkage")
            link_current_observations(
                body["query_observations"], sql, start, start + len(body["query_observations"])
            )
            if batch not in current or body["final"] is not True or batch in final_reads:
                raise ValueError("duplicate or foreign final lease read")
            final_reads[batch] = key
            projection_relations = (
                None
                if original_owner is None
                else FinalRelations(original_owner, {"lease_projection": final["leases:" + batch]})
            )
        for row in rows:
            lease = EnvironmentLease.model_validate(
                {name: row[name] for name in EnvironmentLease.model_fields}
            )
            identity = str(lease.id)
            slot = lease.case_slot
            if (
                str(slot.batch_id) != batch
                or slot.workspace != body["scope"]
                or row["scope_key"] != body["scope"]
            ):
                raise ValueError("historical lease case-slot scope differs")
            attempts = body["attempts"][identity]
            operations = body["operations"][identity]
            read(
                "attempts:" + identity,
                ATTEMPT_SQL,
                {
                    "scope": body["scope"],
                    "batch": UUID(batch),
                    "case": slot.case_id,
                    "config": slot.config_version,
                    "repeat": slot.repeat - 1,
                },
                attempts,
            )
            read(
                "operations:" + identity,
                OPERATION_SQL,
                {"scope": body["scope"], "id": lease.id},
                operations,
            )
            clean = lease_observation_clean(lease, attempts, operations, body["scope"])
            if body["final"] and not clean:
                raise ValueError("original historical final lease unresolved")
            if in_current:
                if actual_leases.get(identity) != row or identity in seen_current:
                    raise ValueError("current complete lease settlement differs")
                if original_owner is None:
                    seen_current.add(identity)
                else:
                    seen_current[identity] = None
                projection = (
                    (value for value in final["leases:" + batch] if value["id"] == identity)
                    if projection_relations is None
                    else projection_relations.all("lease_projection", identity)
                )
                projection_count = 0
                for value in projection:
                    if lease.model_dump(mode="json") != value:
                        raise ValueError("current final lease projection differs")
                    projection_count += 1
                if projection_count != 1:
                    raise ValueError("current final lease projection differs")
                if source_relations is None:
                    expected_attempts = {
                        (str(value["run_id"]), value["attempt"])
                        for value in source["attempts"]
                        if str(value["batch_id"]) == batch
                        and str(value["case_revision_id"]) == str(slot.case_id)
                        and str(value["config_version_id"]) == str(slot.config_version)
                        and value["repetition"] == slot.repeat - 1
                    }
                    attempts_equal = {
                        (str(value["run_id"]), value["attempt"]) for value in attempts
                    } == expected_attempts
                else:
                    from scripts.execution_capacity.replay_relations import legacy_attempt_key

                    def attempt_keys(values):
                        for value in values:
                            yield (
                                legacy_attempt_key(
                                    "run-attempt",
                                    [str(value["run_id"]), value["attempt"]],
                                    owner=original_owner,
                                    budget=budget,
                                ),
                                None,
                            )

                    expected_attempts = mapping(
                        "attempts",
                        attempt_keys(
                            source_relations.all(
                                "source_attempts",
                                (
                                    batch,
                                    str(slot.case_id),
                                    str(slot.config_version),
                                    slot.repeat - 1,
                                ),
                            )
                        ),
                    )
                    observed_attempts = mapping("attempts", attempt_keys(attempts))
                    attempts_equal = observed_attempts.same_keys(expected_attempts)
                if not attempts_equal:
                    raise ValueError("complete historical attempt source differs")
            lease_body = {
                "scope": body["scope"],
                "batch_id": batch,
                "namespace": lease.namespace,
                "environment_version": str(lease.environment_version),
                "generation": lease.generation,
                "case_slot": lease.model_dump(mode="json")["case_slot"],
            }
            if identity in leases and leases[identity] != lease_body:
                raise ValueError("historical lease immutable identity changed")
            leases[identity] = lease_body
            state_key = f"{identity}:{lease.revision}"
            state = {
                "body": {"lease_id": identity, "revision": lease.revision, "state": lease.state},
                "receipt": None,
            }
            if state_key in states and states[state_key] != state:
                raise ValueError("historical lease revision conflicts")
            states[state_key] = state
            for operation in operations:
                if (
                    str(operation["lease_id"]) != identity
                    or operation["scope_key"] != body["scope"]
                    or operation["generation"] != lease.generation
                ):
                    raise ValueError("foreign historical operation")
                raw = plain(operation)
                oid = (
                    str(operation["id"])
                    + ":"
                    + sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
                )
                observations[oid] = {"body": raw, "receipt": None}
        if next(reads, None) is not None or next(observed, None) is not None:
            raise ValueError("extra historical query receipt")
    coverage = (
        set(final_reads) == set(current) and seen_current == set(actual_leases)
        if original_owner is None
        else final_reads.same_keys(current) and seen_current.same_keys(actual_leases)
    )
    if not coverage:
        raise ValueError("complete current final lease read absent")
    if (
        not same_history(records["lease_state"], states)
        or not same_history(records["environment_observation"], observations)
        or (
            set(records["lease"]) != set(leases)
            if original_owner is None
            else not leases.same_keys(records["lease"])
        )
    ):
        raise ValueError("orphan original predicate journal record")
    for identity, body in leases.items():
        record = records["lease"][identity]
        if record != {
            "body": body,
            "receipt": {"state": "verified_clean", "namespace": body["namespace"]},
        }:
            raise ValueError("original historical lease receipt differs")
        if identity not in actual_leases and inherited.get("lease", {}).get(identity) != record:
            raise ValueError("deleted historical lease lacks immutable base evidence")


def link_current_observations(observations, sql, start, end):
    if len(observations) != end - start:
        raise ValueError("complete current historical SQL links differ")
    for index, row in enumerate(observations, start):
        if (
            row["owner"] != "observer"
            or row["sql_index"] != index
            or row["dispatch"] != plain(sql[index])
            or row["parameter_types"] != parameter_types(sql[index]["parameters"])
        ):
            raise ValueError("current lease read lacks actual owner SQL linkage")
