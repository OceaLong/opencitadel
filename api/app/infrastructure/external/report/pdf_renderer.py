#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PDF rendering via weasyprint with graceful degradation."""
from __future__ import annotations


class PdfUnavailableError(RuntimeError):
    """Raised when weasyprint or system libraries are unavailable."""


def render_html_to_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise PdfUnavailableError("weasyprint is not installed") from exc
    return HTML(string=html).write_pdf()
