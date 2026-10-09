"""Small cleanup wire; complete content belongs to the verified original view."""

from scripts.acceptance.capacity_io import strict_json
from scripts.execution_capacity.attempt import encode
from scripts.execution_capacity.c2c_export import _receipt
from scripts.execution_capacity.original_journal import OriginalJournal
from scripts.execution_capacity.original_shards import OriginalView

SCHEMA = "opencitadel.cleanup-envelope.v2"
MAX_BYTES = 16 * 1024


def cleanup_envelope(final, *, budget):
    value = {
        "schema": SCHEMA,
        "root": {"namespace": "local-original-v2", "family": "cleanup", "ordinal": 0},
        "final": final,
    }
    # The independent child ceiling applies before encoder allocation.
    limited = budget.child(bytes_limit=MAX_BYTES, row_limit=MAX_BYTES, rows_limit=1024)
    limited.charge(value)
    raw = encode(value)
    if len(raw) > MAX_BYTES:
        raise ValueError("cleanup envelope exceeds bound")
    return strict_json(raw)


def resolve_cleanup_envelope(value, *, view, final, budget):
    if (
        type(view) is not OriginalView
        or type(getattr(view, "journal", None)) is not OriginalJournal
    ):
        raise ValueError("verified schema2 cleanup original view required")
    view.journal._usable()
    if (
        type(value) is not dict
        or set(value) != {"schema", "root", "final"}
        or value["schema"] != SCHEMA
        or type(value["root"]) is not dict
        or type(value["root"].get("ordinal")) is not int
        or value["root"] != {"namespace": "local-original-v2", "family": "cleanup", "ordinal": 0}
        or type(final) is not dict
        or set(final) != {"manifest", "protocol_id", "config_digest", "identity", "prior", "unit"}
    ):
        raise ValueError("closed cleanup envelope required")
    expected = cleanup_envelope(final, budget=budget)
    budget.child(bytes_limit=MAX_BYTES, row_limit=MAX_BYTES, rows_limit=1024).charge(value)
    if encode(value) != encode(expected):
        raise ValueError("cleanup envelope final differs")
    if (
        view.manifest["binding"] != {key: item for key, item in final.items() if key != "manifest"}
        or _receipt(view.root / "manifest.json", budget) != final["manifest"]
    ):
        raise ValueError("cleanup envelope original binding differs")
    return view.materialize()["cleanup"]
