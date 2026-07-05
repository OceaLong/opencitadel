#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.services.document_service import _parse_pdf


def _make_blank_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "hello")
    data = doc.tobytes()
    doc.close()
    return data


def test_parse_pdf_renders_pages_with_fitz():
    data = _make_blank_pdf()
    combined, pages = _parse_pdf(data, max_pages=5)

    assert pages
    assert all(page.get("image_base64") for page in pages)
    assert all(page.get("mime_type") == "image/jpeg" for page in pages)
    assert "hello" in combined.lower() or any("hello" in (page.get("text") or "").lower() for page in pages)


def test_parse_pdf_prefers_fitz_when_pypdf_would_fail(monkeypatch):
    data = _make_blank_pdf()

    def _fail_pypdf(*_args, **_kwargs):
        raise AssertionError("pypdf path should not run when fitz succeeds")

    monkeypatch.setattr(
        "app.domain.services.document_service._parse_pdf_with_pypdf",
        _fail_pypdf,
    )
    _, pages = _parse_pdf(data, max_pages=5)
    assert pages and pages[0].get("image_base64")
