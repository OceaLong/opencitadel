#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Non-recoverable knowledge-base ingestion failures."""
from app.domain.models.error_codes import DOCUMENT_PARSE_FAILED


class NonRecoverableIngestError(RuntimeError):
    """Ingest failure that should not be retried (e.g. corrupt document)."""

    def __init__(self, message: str, *, error_code: str = DOCUMENT_PARSE_FAILED) -> None:
        super().__init__(message)
        self.error_code = error_code
