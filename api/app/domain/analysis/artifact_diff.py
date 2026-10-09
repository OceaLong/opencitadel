"""Whole-input text/JSON differences with explicit input, work and output ceilings."""

import json
from dataclasses import dataclass, field
from difflib import unified_diff
from math import isfinite

PREVIEW_BYTES = 65536


@dataclass(frozen=True)
class DiffResult:
    content_changed: bool | None
    complete: bool
    reason: str | None = None
    content: str = ""
    operations: list[dict] = field(default_factory=list)


def text_diff(before: str, after: str) -> str:
    return "".join(
        unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="baseline",
            tofile="candidate",
        )
    )


def _budget(before, after, output_limit, input_limit):
    if type(output_limit) is not int or not 1 <= output_limit <= 1048576:
        raise ValueError("invalid_diff_output_limit")
    if type(input_limit) is not int or not 1 <= input_limit <= 4194304:
        raise ValueError("invalid_diff_input_limit")
    return len(before.encode()) + len(after.encode()) <= input_limit


def compare_text(before, after, *, output_limit=PREVIEW_BYTES, input_limit=PREVIEW_BYTES):
    if not _budget(before, after, output_limit, input_limit):
        return DiffResult(None, False, "async_required")
    if before == after:
        return DiffResult(False, True)
    # Bound SequenceMatcher worst-case line comparisons before entering difflib.
    if (before.count("\n") + 1) * (after.count("\n") + 1) > 1000000:
        return DiffResult(True, False, "async_required")
    body = text_diff(before, after)
    encoded = body.encode()
    if len(encoded) > output_limit:
        return DiffResult(
            True, False, "output_limit", encoded[:output_limit].decode(errors="ignore")
        )
    return DiffResult(True, True, content=body)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous_json_key")
        result[key] = value
    return result


def _parse(body):
    def reject(value):
        raise ValueError("nonfinite_json")

    value = json.loads(body, object_pairs_hook=_object, parse_constant=reject)
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 64 or nodes > 10000 or (isinstance(item, float) and not isfinite(item)):
            raise ValueError("json_complexity")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def compare_json(before, after, *, output_limit=PREVIEW_BYTES, input_limit=PREVIEW_BYTES):
    if not _budget(before, after, output_limit, input_limit):
        return DiffResult(None, False, "async_required")
    try:
        left, right = _parse(before), _parse(after)
    except (ValueError, RecursionError):
        return DiffResult(None, False, "invalid_or_complex_json")
    operations = []

    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):

            def child(key):
                return path + "/" + key.replace("~", "~0").replace("/", "~1")

            operations.extend(
                {"op": "remove", "path": child(key)} for key in sorted(a.keys() - b.keys())
            )
            operations.extend(
                {"op": "add", "path": child(key), "value": b[key]}
                for key in sorted(b.keys() - a.keys())
            )
            for key in sorted(a.keys() & b.keys()):
                walk(a[key], b[key], child(key))
        elif json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            operations.append({"op": "replace", "path": path, "value": b})

    walk(left, right, "")
    output, size = [], 2
    for operation in operations:
        size += len(json.dumps(operation, ensure_ascii=False).encode()) + 2
        if size > output_limit or len(output) >= 1000:
            return DiffResult(bool(operations), False, "output_limit", operations=output)
        output.append(operation)
    return DiffResult(bool(operations), True, operations=output)
