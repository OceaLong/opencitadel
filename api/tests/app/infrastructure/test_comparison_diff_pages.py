"""The persistent page budget includes UTF-8 boundaries and metadata."""

import json

import pytest

from app.infrastructure.repositories.db_comparison_diff_jobs import diff_pages


def result(content):
    return {
        "left": {"version": 1},
        "right": {"version": 2},
        "format": "text",
        "diff": {
            "content_changed": True,
            "complete": True,
            "reason": None,
            "content": content,
            "operations": [],
        },
    }


def test_multibyte_output_round_trips_inside_page_and_total_budgets():
    value = result("界🙂" * 20000)
    retained, pages = diff_pages(value)
    assert json.loads("".join(pages)) == retained == value
    assert len(pages) <= 16
    assert all(len(page.encode()) <= 65536 for page in pages)


def test_oversized_output_is_explicit_partial_without_changed_claim_loss():
    retained, pages = diff_pages(result("x" * 1048576))
    assert retained["diff"]["reason"] == "output_limit"
    assert retained["diff"]["complete"] is False
    assert retained["diff"]["content_changed"] is True
    assert len(pages) <= 16


def test_oversized_metadata_cannot_escape_the_total_budget():
    value = result("")
    value["left"]["malformed"] = "x" * 1048576
    with pytest.raises(ValueError, match="output_limit"):
        diff_pages(value)
