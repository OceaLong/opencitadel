"""Typed export output must remain data in both spreadsheet and JSON consumers."""

import csv
import io
import json
from decimal import Decimal

import pytest

from app.application.services.execution_export_encoding import ExportColumn, encode_export
from app.application.services.execution_export_service import csv_cell


@pytest.mark.parametrize(
    "text", ["=SUM(A1:A2)", " +cmd", "-123", "@SUM(A1)", "\tvalue", "\rvalue", "\n =1"]
)
def test_csv_export_does_not_turn_user_text_into_formula(text):
    assert csv_cell(text) == "'" + text


def test_csv_export_preserves_ordinary_text():
    assert csv_cell("normal") == "normal"


def test_typed_csv_keeps_numbers_and_metadata_rectangular():
    columns = (
        ExportColumn("label", "text"),
        ExportColumn("amount", "number"),
        ExportColumn("missing", "number"),
    )
    raw = b"".join(
        encode_export(
            "csv",
            {"table_kind": "runs"},
            {"sample_count": 1},
            columns,
            [{"label": "-123", "amount": Decimal("-12.50"), "missing": None}],
        )
    )
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    assert [r["record_type"] for r in rows] == ["metadata", "metrics", "run"]
    assert rows[2]["label"] == "'-123"
    assert rows[2]["amount"] == "-12.50"
    assert rows[2]["missing"] == ""
    assert json.loads(rows[0]["metadata_json"])["table_kind"] == "runs"
    assert json.loads(rows[1]["metrics_json"]) == {"sample_count": 1}


def test_json_decimal_is_a_number_and_missing_is_null():
    columns = (ExportColumn("money", "number"), ExportColumn("missing", "number"))
    result = json.loads(
        b"".join(
            encode_export(
                "json",
                {"table_kind": "runs"},
                {},
                columns,
                [{"money": Decimal("0.123456789123456789"), "missing": None}],
            )
        ),
        parse_float=Decimal,
    )
    assert result["schema_version"] == "execution-export-v1"
    assert result["rows"] == [{"money": Decimal("0.123456789123456789"), "missing": None}]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN"), "12", True])
def test_numeric_columns_reject_nonfinite_or_untyped_values(value):
    with pytest.raises(ValueError, match="export_invalid_cell"):
        list(
            encode_export(
                "json", {"table_kind": "runs"}, {}, (ExportColumn("n", "number"),), [{"n": value}]
            )
        )


def test_unknown_row_fields_cannot_leak_private_payload():
    with pytest.raises(ValueError, match="export_invalid_columns"):
        list(
            encode_export(
                "json",
                {"table_kind": "runs"},
                {},
                (ExportColumn("label", "text"),),
                [{"label": "ok", "private_payload": "secret"}],
            )
        )


def test_output_limit_fails_without_truncating():
    with pytest.raises(ValueError, match="export_capacity_exceeded"):
        list(
            encode_export(
                "csv",
                {"table_kind": "runs"},
                {},
                (ExportColumn("label", "text"),),
                [{"label": "x" * 100}],
                max_output_bytes=80,
            )
        )


def test_scalar_text_limit_is_utf8_bytes():
    with pytest.raises(ValueError, match="export_capacity_exceeded"):
        list(
            encode_export(
                "json",
                {"table_kind": "runs"},
                {},
                (ExportColumn("label", "text"),),
                [{"label": "界" * 1366}],
            )
        )


@pytest.mark.asyncio
async def test_request_replay_fingerprint_does_not_change_with_workspace_preference():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.application.services.execution_export_service import ExecutionExportService
    from app.domain.models.scope import Principal

    repo = SimpleNamespace(
        accept=AsyncMock(return_value={"id": "job"}),
        get=AsyncMock(return_value={"id": "job", "status": "queued"}),
    )
    service = ExecutionExportService(repo, None, workspace_timezone="Asia/Shanghai")
    payload = {
        "source_kind": "filter",
        "format": "csv",
        "request_id": "same",
        "selection": {"mode": "all_matching", "timezone": "UTC"},
    }
    await service.create(None, Principal(user_id="u"), payload)
    first = repo.accept.await_args.args[2]
    service.workspace_timezone = "America/New_York"
    await service.create(None, Principal(user_id="u"), payload)
    second = repo.accept.await_args.args[2]
    assert first["request_fingerprint"] == second["request_fingerprint"]
    assert first["selection"]["timezone"] != second["selection"]["timezone"]
    with pytest.raises(ValueError, match="invalid_analysis_timezone"):
        await service.create(
            None, Principal(user_id="u"), {**payload, "selection": {"timezone": "invalid/zone"}}
        )
