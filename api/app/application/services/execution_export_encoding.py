"""Bounded deterministic UTF-8 encoders for fixed, allowlisted export rows.

Decimal values are emitted as JSON numbers without a float round trip. This module
has no source access: callers supply immutable captured facts and explicit columns.
"""

import csv
import io
import json
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Literal

from app.application.services.execution_export_service import csv_cell

SCHEMA_VERSION = "execution-export-v1"
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
MAX_ROW_BYTES = 256 * 1024
MAX_JSON_CELL_BYTES = 128 * 1024
MAX_TEXT_BYTES = 4096


@dataclass(frozen=True)
class ExportColumn:
    name: str
    kind: Literal["text", "number", "integer", "boolean", "score", "json"]


def json_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("export_invalid_cell")  # noqa: TRY004 - stable export validation code
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("export_invalid_cell")
    elif isinstance(value, float) and not isfinite(value):
        raise ValueError("export_invalid_cell")
    return str(value)


def safe_json(value):
    """Serialize a finite JSON tree; reject custom objects and non-string keys."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return json_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (tuple, list)):
        return "[" + ",".join(safe_json(v) for v in value) + "]"
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return "{" + ",".join(safe_json(k) + ":" + safe_json(value[k]) for k in sorted(value)) + "}"
    raise ValueError("export_invalid_cell")


def bounded(text, maximum):
    if len(text.encode("utf-8")) > maximum:
        raise ValueError("export_capacity_exceeded")
    return text


def cell(value, column, *, csv_format):
    if value is None:
        return "" if csv_format else "null"
    if column.kind == "text":
        if not isinstance(value, str):
            raise ValueError("export_invalid_cell")
        bounded(value, MAX_TEXT_BYTES)
        return csv_cell(value) if csv_format else safe_json(value)
    if column.kind == "json":
        text = bounded(safe_json(value), MAX_JSON_CELL_BYTES)
        return csv_cell(text) if csv_format else text
    if column.kind == "boolean" or (column.kind == "score" and isinstance(value, bool)):
        if not isinstance(value, bool):
            raise ValueError("export_invalid_cell")
        return "true" if value else "false"
    if column.kind in {"integer", "score"} and type(value) is not int:
        raise ValueError("export_invalid_cell")
    if column.kind not in {"number", "integer", "score"}:
        raise ValueError("export_invalid_cell")
    return json_number(value)


def csv_record(values):
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerow(values)
    return output.getvalue()


class ExportEncoder:
    """Incremental encoder permits asynchronous bounded-page row producers."""

    def __init__(self, format, metadata, metrics, columns, *, max_output_bytes=MAX_OUTPUT_BYTES):
        if format not in {"csv", "json"} or metadata.get("table_kind") not in {
            "runs",
            "batch_results",
        }:
            raise ValueError("export_invalid_format")
        self.format, self.metadata = format, metadata
        self.columns = tuple(columns)
        self.names = [c.name for c in self.columns]
        if (
            len(self.names) > 61
            or len(set(self.names)) != len(self.names)
            or set(self.names) & {"record_type", "metadata_json", "metrics_json"}
        ):
            raise ValueError("export_invalid_columns")
        self.metadata_json = bounded(safe_json(metadata), 1024 * 1024)
        self.metrics_json = bounded(safe_json(metrics), 8 * 1024 * 1024)
        self.count, self.total = 0, 0
        self.max_output_bytes = min(max_output_bytes, MAX_OUTPUT_BYTES)

    def output(self, text):
        data = text.encode("utf-8")
        self.total += len(data)
        if self.total > self.max_output_bytes:
            raise ValueError("export_capacity_exceeded")
        return data

    def header(self):
        if self.format == "csv":
            yield self.output(
                csv_record(["record_type", "metadata_json", "metrics_json", *self.names])
            )
            yield self.output(
                csv_record(
                    ["metadata", csv_cell(self.metadata_json), "", *([""] * len(self.names))]
                )
            )
            yield self.output(
                csv_record(["metrics", "", csv_cell(self.metrics_json), *([""] * len(self.names))])
            )
        else:
            yield self.output(
                '{"schema_version":"'
                + SCHEMA_VERSION
                + '","metadata":'
                + self.metadata_json
                + ',"metrics":'
                + self.metrics_json
                + ',"rows":['
            )

    def row(self, row):
        if set(row) != set(self.names):
            raise ValueError("export_invalid_columns")
        self.count += 1
        if self.count > (100000 if self.metadata["table_kind"] == "runs" else 5000):
            raise ValueError("export_capacity_exceeded")
        values = [cell(row[c.name], c, csv_format=self.format == "csv") for c in self.columns]
        if self.format == "csv":
            record = csv_record(
                ["run" if self.metadata["table_kind"] == "runs" else "case_result", "", "", *values]
            )
        else:
            record = (
                ("," if self.count > 1 else "")
                + "{"
                + ",".join(safe_json(n) + ":" + v for n, v in zip(self.names, values, strict=True))
                + "}"
            )
        return self.output(bounded(record, MAX_ROW_BYTES))

    def finish(self):
        if "data_row_count" in self.metadata and self.metadata["data_row_count"] != self.count:
            raise ValueError("export_capture_mismatch")
        if self.format == "json":
            yield self.output("]}")


def encode_export(format, metadata, metrics, columns, rows, *, max_output_bytes=MAX_OUTPUT_BYTES):
    encoder = ExportEncoder(format, metadata, metrics, columns, max_output_bytes=max_output_bytes)
    yield from encoder.header()
    for row in rows:
        yield encoder.row(row)
    yield from encoder.finish()
