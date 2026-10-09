import csv
import io
import json


def parse(data, content_type="application/json"):
    from app.application.evaluation.import_parser import parse_import

    return parse_import(io.BytesIO(data), content_type=content_type)


def test_error_at_999_keeps_exact_row_field_and_no_applicable_cases():
    cases = [{"case_key": str(i), "input": "x"} for i in range(1000)]
    cases[998]["input"] = [{"role": "system", "content": "bad"}]
    result = parse(json.dumps({"schema_version": 1, "cases": cases}).encode())
    assert any(e.row == 999 and e.field == "input.0.role" for e in result.errors)
    assert not result.cases


def test_json_duplicate_keys_schema_and_limits():
    assert (
        parse(b'{"schema_version":1,"schema_version":1,"cases":[]}').errors[0].code
        == "duplicate_json_key"
    )
    assert parse(b'{"schema_version":2,"cases":[]}').errors[0].code == "schema_version"
    rows = [{"case_key": str(i), "input": "x"} for i in range(1001)]
    assert (
        parse(json.dumps({"schema_version": 1, "cases": rows}).encode()).errors[0].code
        == "case_limit_exceeded"
    )


def test_csv_exact_schema_and_complex_columns():
    from app.application.evaluation.import_parser import CSV_COLUMNS

    body = io.StringIO()
    writer = csv.writer(body)
    writer.writerow(CSV_COLUMNS)
    writer.writerow(["a", "question", "", '["tag"]', "[]", "[]", "[]"])
    result = parse(body.getvalue().encode(), "text/csv")
    assert not result.errors
    assert result.cases[0].tags == ("tag",)
    assert parse(b"case_key,input\na,x\n", "text/csv").errors[0].code == "csv_columns"


def test_size_is_checked_by_bounded_reads_without_trusting_headers():
    from app.application.evaluation.import_parser import parse_import
    from app.domain.evaluation.dataset import MAX_IMPORT_BYTES

    class Body:
        remaining = MAX_IMPORT_BYTES + 1

        def read(self, count):
            assert 0 < count <= 65536
            size = min(self.remaining, count)
            self.remaining -= size
            return b" " * size

    assert (
        parse_import(Body(), content_type="application/json").errors[0].code
        == "byte_limit_exceeded"
    )


def test_duplicate_case_reports_second_row_and_digest_tracks_exact_bytes():
    data = (
        b'{"schema_version":1,"cases":[{"case_key":"a","input":"x"},{"case_key":"a","input":"y"}]}'
    )
    result = parse(data)
    assert [(e.row, e.field, e.code) for e in result.errors] == [
        (2, "case_key", "duplicate_case_key")
    ]
    import hashlib

    assert result.digest == hashlib.sha256(data).hexdigest()


def test_csv_large_field_within_raw_limit_and_malformed_row_location():
    from app.application.evaluation.import_parser import CSV_COLUMNS

    body = io.StringIO()
    writer = csv.writer(body)
    writer.writerow(CSV_COLUMNS)
    writer.writerow(["a", "x" * 140000, "", "[]", "[]", "[]", "[]"])
    parsed = parse(body.getvalue().encode(), "text/csv")
    assert not parsed.errors
    assert parsed.cases[0].input == "x" * 140000
    malformed = (",".join(CSV_COLUMNS) + '\na,"unterminated').encode()
    result = parse(malformed, "text/csv")
    assert [(e.code, e.row) for e in result.errors] == [("invalid_csv", 2)]
