"""Complete private broker operations AND bindings, with original request authority.

SQLite readback is a readonly snapshot, not writer-exit evidence. Unknown old
claims remain independent identities even if the current lease is clean.
"""

import hashlib
import inspect
import json
import sqlite3
import uuid
from dataclasses import dataclass, field

from app.domain.evaluation.configuration import digest


def sqlite_pages(
    path, page_size=1000, *, row_limit=1024 * 1024, bytes_limit=16 * 1024 * 1024, rows_limit=16384
):
    """This standalone function is also the fixed in-broker read-only command."""
    if not 1 <= page_size <= 1000:
        raise ValueError("invalid broker page size")
    snapshot = str(uuid.uuid4())
    with sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        counts, input_bytes, input_rows = {}, 0, 0
        for table in ("operations", "bindings"):
            columns = (
                ("identity", "fingerprint", "result")
                if table == "operations"
                else ("identity", "fingerprint")
            )
            sizes = "+".join("coalesce(length(CAST(" + name + " AS BLOB)),0)" for name in columns)
            invalid = "typeof(identity)<>'text' OR length(identity)=0 OR instr(identity,char(0))>0 OR typeof(fingerprint)<>'text'"
            if table == "operations":
                invalid += " OR typeof(result) NOT IN ('text','null')"
            count, largest, total, bad = db.execute(
                "SELECT count(*),coalesce(max("
                + sizes
                + "),0),coalesce(sum("
                + sizes
                + "),0),coalesce(sum(CASE WHEN "
                + invalid
                + " THEN 1 ELSE 0 END),0) FROM "
                + table
            ).fetchone()
            if (
                bad
                or any(type(n) is not int or n < 0 for n in (count, largest, total))
                or ((count == 0) != (largest == total == 0))
            ):
                raise ValueError("invalid original broker storage")
            input_bytes += total
            input_rows += count
            if largest > row_limit or input_bytes > bytes_limit or input_rows > rows_limit:
                raise ValueError("original broker input quota exceeded")
            counts[table] = count
        yield {"kind": "begin", "snapshot": snapshot, "counts": counts}
        for table in ("operations", "bindings"):
            columns = (
                "identity,fingerprint,result" if table == "operations" else "identity,fingerprint"
            )
            cursor = db.execute(
                "SELECT " + columns + " FROM " + table + " ORDER BY identity COLLATE BINARY"
            )
            ordinal, rows, estimated = 0, [], 2

            def page(values, index, table=table):
                body = json.dumps(
                    values, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
                return {
                    "kind": table,
                    "snapshot": snapshot,
                    "ordinal": index,
                    "count": len(values),
                    "rows": values,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "terminal": not values,
                }

            for row in cursor:
                # Worst-case escaped JSON, wrapper and digest allowance is
                # checked before decoding/copying this bounded original row.
                required = 512 + sum(len(part) * 12 for part in row if part is not None)
                if required > 1024 * 1024 - 1024:
                    raise ValueError("original broker frame quota exceeded")
                if rows and (len(rows) == page_size or estimated + required > 1024 * 1024 - 1024):
                    yield page(rows, ordinal)
                    ordinal += 1
                    rows, estimated = [], 2
                value = {"identity": row[0], "fingerprint": row[1]}
                if table == "operations":
                    value["result_raw"] = row[2]
                    value["result_sha256"] = (
                        None
                        if row[2] is None
                        else hashlib.sha256(
                            json.dumps(
                                json.loads(row[2]),
                                sort_keys=True,
                                separators=(",", ":"),
                                allow_nan=False,
                            ).encode()
                        ).hexdigest()
                    )
                rows.append(value)
                estimated += required
            if rows:
                yield page(rows, ordinal)
                ordinal += 1
            yield page([], ordinal)
        yield {"kind": "complete", "snapshot": snapshot, "counts": counts}
        db.rollback()


# Fixed source and fixed path; no arbitrary caller code/path crosses Docker exec.
BROKER_READ = (
    "import hashlib,json,sqlite3,uuid\n"
    + inspect.getsource(sqlite_pages)
    + "\nfor page in sqlite_pages('/var/lib/opencitadel-evaluation/operations.sqlite'):\n print(json.dumps(page,separators=(',',':')),flush=True)\n"
)


@dataclass
class BrokerInventory:
    operations: dict = field(default_factory=dict)
    bindings: dict = field(default_factory=dict)
    pages: list = field(default_factory=list)
    complete: bool = False
    errors: list = field(default_factory=list)


def parse_pages(pages, *, budget=None):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget

    budget = budget if budget is not None else EvidenceBudget()
    result = BrokerInventory()
    try:
        iterator = iter(pages)
        begin = next(iterator)
        if (
            begin["kind"] != "begin"
            or set(begin["counts"]) != {"operations", "bindings"}
            or any(type(n) is not int or n < 0 for n in begin["counts"].values())
        ):
            raise ValueError("missing broker snapshot/count authority")
        snapshot, counts = begin["snapshot"], begin["counts"]
        uuid.UUID(snapshot)
        budget.charge(begin)
        result.pages.append(begin)
        for table in ("operations", "bindings"):
            ordinal, previous = 0, None
            while True:
                page = next(iterator)
                budget.charge(page)
                budget.reserve(len(page.get("rows", ())) * 256, rows=0)
                result.pages.append(page)
                rows = page["rows"]
                raw = json.dumps(
                    rows, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
                if (
                    page["kind"] != table
                    or page["snapshot"] != snapshot
                    or page["ordinal"] != ordinal
                    or page["count"] != len(rows)
                    or len(rows) > 1000
                    or hashlib.sha256(raw).hexdigest() != page["sha256"]
                    or page["terminal"] is not (not rows)
                ):
                    raise ValueError("invalid broker pagination")
                ordinal += 1
                for row in rows:
                    identity = row["identity"]
                    if (
                        not isinstance(identity, str)
                        or not identity
                        or (previous is not None and identity.encode() <= previous.encode())
                    ):
                        raise ValueError("duplicate/nonadvancing broker identity")
                    if (
                        not isinstance(row["fingerprint"], str)
                        or len(row["fingerprint"]) != 64
                        or any(c not in "0123456789abcdef" for c in row["fingerprint"])
                    ):
                        raise ValueError("invalid broker fingerprint")
                    if table == "operations":
                        receipt = row["result_sha256"]
                        original = row["result_raw"]
                        if original is not None and not isinstance(original, str):
                            raise ValueError("invalid original broker result type")
                        if original is not None:
                            budget.reserve(len(original) * 64, rows=1)
                        if (None if original is None else digest(json.loads(original))) != receipt:
                            raise ValueError("original broker result commitment differs")
                        if receipt is not None and (
                            len(receipt) != 64 or any(c not in "0123456789abcdef" for c in receipt)
                        ):
                            raise ValueError("invalid broker receipt")
                        result.operations[identity] = {
                            k: row[k] for k in ("fingerprint", "result_sha256")
                        }
                    else:
                        result.bindings[identity] = row["fingerprint"]
                    previous = identity
                if page["terminal"]:
                    break
            if len(getattr(result, table)) != counts[table]:
                raise ValueError("broker snapshot cardinality differs")
        final = next(iterator)
        budget.charge(final)
        result.pages.append(final)
        if (
            final != {"kind": "complete", "snapshot": snapshot, "counts": counts}
            or next(iterator, None) is not None
        ):
            raise ValueError("missing/foreign broker exhaustion")
        result.complete = True
    except Exception as error:  # noqa: BLE001 - retain failed acquisition and continue safe cleanup
        result.errors.append({"stage": "broker-read", "type": type(error).__name__})
    return result


def read_broker(binding, docker, *, budget=None):
    raw = docker("exec", binding["broker"]["container_id"], "python", "-c", BROKER_READ)
    # Docker transport failures propagate; partial stdout is never clean.
    if raw and not raw.endswith(b"\n"):
        raise ValueError("incomplete broker transport frame")
    import io

    return parse_pages((json.loads(line) for line in io.BytesIO(raw)), budget=budget)


def request_identity(body):
    lease, operation = body["lease"], body["operation"]
    return f"{lease['case_slot']['workspace']}:{lease['id']}:{lease['generation']}:{operation['id']}:{operation['claim_generation']}"


class BrokerRequests:
    def __init__(self, journal):
        self.journal = journal

    def before(self, body):
        identity = request_identity(body)
        self.journal.intent("broker_request", identity, {"request": body})
        # Immutable exact body commits before HTTP dispatch. Never includes token.
        return identity

    def after(self, identity, receipt):
        self.journal.acknowledge("broker_request", identity, {"result": receipt})


def expected_requests(journal):
    result = BrokerInventory(complete=True)
    for identity, record in journal.records("broker_request"):
        body = record["body"]["request"]
        if request_identity(body) != identity:
            raise ValueError("original broker request identity differs")
        lease = body["lease"]
        key = f"{lease['id']}:{lease['generation']}"
        binding = digest({"slot": lease["case_slot"], "version": body["version"]})
        if key in result.bindings and result.bindings[key] != binding:
            raise ValueError("original pinned slot/version changed")
        result.bindings[key] = binding
        receipt = record["receipt"]
        result.operations[identity] = {
            "fingerprint": digest(body),
            "result_sha256": None
            if receipt is None
            else hashlib.sha256(
                json.dumps(
                    receipt["result"], sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            ).hexdigest(),
            "state": "pending" if receipt is None else "settled",
            "request": body,
        }
    return result


def reconcile(actual, expected):
    issues = []
    if not actual.complete or actual.errors:
        issues.append({"identity": "broker-snapshot", "state": "error"})
    issues.extend(
        {"identity": key, "state": "error", "kind": "binding"}
        for key in sorted(set(actual.bindings) | set(expected.bindings))
        if actual.bindings.get(key) != expected.bindings.get(key)
    )
    for key in sorted(set(actual.operations) | set(expected.operations)):
        observed, original = actual.operations.get(key), expected.operations.get(key)
        if original is None or observed is None:
            state = "error"
        elif original["state"] == "pending" or observed["result_sha256"] is None:
            state = "pending"
        elif any(observed[k] != original[k] for k in ("fingerprint", "result_sha256")):
            state = "error"
        else:
            continue
        issues.append({"identity": key, "state": state, "kind": "operation"})
    return issues


def request_parents(expected, journal):
    """Join original serialization to actual cumulative DB lease/claim readbacks."""
    observations = [row["body"] for _, row in journal.records("environment_observation")]
    for original in expected.operations.values():
        request = original["request"]
        lease, operation = request["lease"], request["operation"]
        parent = journal.parent("lease", lease["id"])
        if (
            parent["case_slot"] != lease["case_slot"]
            or parent["scope"] != lease["case_slot"]["workspace"]
            or parent["environment_version"] != lease["environment_version"]
            or parent["generation"] != lease["generation"]
        ):
            raise ValueError("original request pinned slot/version differs from DB lease")
        matches = [
            r
            for r in observations
            if str(r["id"]) == operation["id"]
            and str(r["lease_id"]) == lease["id"]
            and r["generation"] == lease["generation"]
            and r["claim_generation"] == operation["claim_generation"]
        ]
        if not matches or any(
            any(r.get(k) != operation[k] for k in ("phase", "lease_revision")) for r in matches
        ):
            raise ValueError("original request operation/claim missing from exact DB history")
    return expected
