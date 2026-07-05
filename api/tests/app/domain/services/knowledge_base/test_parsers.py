#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.services.knowledge_base.parsers import validate_pdf_integrity


def test_validate_pdf_integrity_rejects_missing_header():
    with pytest.raises(ValueError, match="缺少 PDF 文件头"):
        validate_pdf_integrity(b"not-a-pdf", expected_size=100)


def test_validate_pdf_integrity_rejects_truncated_download():
    with pytest.raises(ValueError, match="下载大小"):
        validate_pdf_integrity(b"%PDF-1.4 truncated", expected_size=1024)


def test_validate_pdf_integrity_accepts_valid_bytes():
    data = b"%PDF-1.4 ok"
    validate_pdf_integrity(data, expected_size=len(data))
