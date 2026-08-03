import pytest

from opencitadel_ops_collector.redaction import redact, redact_text


@pytest.mark.parametrize(
    "source",
    [
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "password=super-secret-value",
        "postgresql://admin:secret@database:5432/app",
        "Cookie: session=topsecret",
        "token: eyJabcdefghijklmno.abcdefghijk.abcdefgh",
    ],
)
def test_secret_shapes_are_redacted(source):
    assert "REDACTED" in redact_text(source)
    assert "secret-value" not in redact_text(source)


def test_sensitive_dictionary_keys_are_redacted_recursively():
    assert redact({"nested": {"api_token": "value"}})["nested"]["api_token"] == "***REDACTED***"
