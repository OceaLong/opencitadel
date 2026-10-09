"""Bounded fixed-schema import validation. No partial applicable imports."""

import csv
import hashlib
import io
import json
import tempfile
from dataclasses import dataclass
from typing import BinaryIO

from pydantic import ValidationError

from app.domain.evaluation.dataset import (
    MAX_IMPORT_BYTES,
    MAX_IMPORT_CASES,
    CaseRevision,
    ImmutableModel,
)

CSV_COLUMNS = (
    "case_key",
    "input",
    "reference_answer",
    "tags",
    "rules_json",
    "attachment_ids_json",
    "knowledge_bindings_json",
)
MAX_ERRORS = 1000
# Process-wide, stable parser policy set once during module initialization.
# The raw byte bound is enforced before CSV parsing; never toggle per request.
csv.field_size_limit(MAX_IMPORT_BYTES)


class ImportErrorItem(ImmutableModel):
    row: int | None = None
    field: str | None = None
    code: str
    message: str


@dataclass(frozen=True)
class ParsedImport:
    digest: str
    data: bytes
    cases: tuple[CaseRevision, ...]
    errors: tuple[ImportErrorItem, ...]
    source_rows: tuple[int, ...] = ()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _json(raw):
    return json.loads(
        raw,
        object_pairs_hook=_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_json")),
    )


def parse_import(stream: BinaryIO, *, content_type: str) -> ParsedImport:
    digest = hashlib.sha256()
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as spool:
        count = 0
        while chunk := stream.read(65536):
            count += len(chunk)
            if count > MAX_IMPORT_BYTES:
                return ParsedImport(
                    "",
                    b"",
                    (),
                    (ImportErrorItem(code="byte_limit_exceeded", message="Import exceeds 20 MiB"),),
                )
            digest.update(chunk)
            spool.write(chunk)
        spool.seek(0)
        data = spool.read()
    errors = []
    cases = []

    def error(code, row=None, field=None):
        if len(errors) < MAX_ERRORS:
            errors.append(
                ImportErrorItem(row=row, field=field, code=code, message=code.replace("_", " "))
            )

    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        error("invalid_utf8")
        return ParsedImport(digest.hexdigest(), data, (), tuple(errors))
    rows = []
    previous_line = 0
    media = content_type.partition(";")[0].strip()
    try:
        if media == "application/json":
            root = _json(text)
            if (
                not isinstance(root, dict)
                or type(root.get("schema_version")) is not int
                or root.get("schema_version") != 1
            ):
                error("schema_version")
            elif set(root) != {"schema_version", "cases"} or not isinstance(root["cases"], list):
                error("import_shape")
            elif len(root["cases"]) > MAX_IMPORT_CASES:
                error("case_limit_exceeded")
            else:
                rows = list(enumerate(root["cases"], 1))
        elif media == "text/csv":
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            if tuple(next(reader, ())) != CSV_COLUMNS:
                error("csv_columns", 1)
            else:
                previous_line = reader.line_num
                for index, values in enumerate(reader):
                    row = previous_line + 1
                    previous_line = reader.line_num
                    if index >= MAX_IMPORT_CASES:
                        error("case_limit_exceeded", row)
                        break
                    if len(values) != len(CSV_COLUMNS):
                        error("csv_columns", row)
                        continue
                    item = dict(zip(CSV_COLUMNS, values, strict=True))
                    mapped = {
                        "case_key": item["case_key"],
                        "input": item["input"],
                        "reference_answer": item["reference_answer"] or None,
                        "reference_confirmed": bool(item["reference_answer"]),
                    }
                    for source, target in (
                        ("tags", "tags"),
                        ("rules_json", "rules"),
                        ("attachment_ids_json", "attachments"),
                        ("knowledge_bindings_json", "knowledge_bindings"),
                    ):
                        try:
                            mapped[target] = _json(item[source])
                        except (ValueError, RecursionError):
                            error("invalid_json", row, source)
                    rows.append((row, mapped))
        else:
            error("unsupported_content_type")
    except csv.Error:
        error("invalid_csv", previous_line + 1)
    except (ValueError, RecursionError) as exc:
        error("duplicate_json_key" if str(exc) == "duplicate_json_key" else "invalid_json")
    seen = set()
    for row, raw in rows:
        if not isinstance(raw, dict):
            error("case_shape", row)
            continue
        # Imports cannot invent historical provenance or immutable identities.
        allowed = {
            "case_key",
            "input",
            "history",
            "attachments",
            "knowledge_bindings",
            "reference_answer",
            "reference_confirmed",
            "rules",
            "tags",
            "applicable_dimensions",
        }
        for field in raw.keys() - allowed:
            error("unknown_field", row, field)
        try:
            case = CaseRevision.model_validate(raw)
        except ValidationError as exc:
            for detail in exc.errors(include_input=False, include_url=False):
                path = [
                    part
                    for part in detail["loc"]
                    if part != "str" and not str(part).startswith("tuple[")
                ]
                error(detail["type"], row, ".".join(str(part) for part in path))
            continue
        if case.case_key in seen:
            error("duplicate_case_key", row, "case_key")
        seen.add(case.case_key)
        cases.append(case)
    return ParsedImport(
        digest.hexdigest(),
        data,
        () if errors else tuple(cases),
        tuple(errors),
        tuple(row for row, _ in rows),
    )
