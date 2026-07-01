#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared tokenizer for Chinese-friendly PostgreSQL simple tsvector."""
import re

import jieba

_SPACE_RE = re.compile(r"\s+")


def segment_for_bm25(text: str) -> str:
    if not text:
        return ""
    tokens = [token.strip() for token in jieba.cut(text) if token.strip()]
    return _SPACE_RE.sub(" ", " ".join(tokens)).strip()
