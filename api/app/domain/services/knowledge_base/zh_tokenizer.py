"""Shared tokenizer for Chinese-friendly PostgreSQL simple tsvector."""

import re

_SPACE_RE = re.compile(r"\s+")


def segment_for_bm25(text: str) -> str:
    if not text:
        return ""
    # 延迟导入:jieba 是 worker 专用重库,api 进程不装
    import jieba

    tokens = [token.strip() for token in jieba.cut(text) if token.strip()]
    return _SPACE_RE.sub(" ", " ".join(tokens)).strip()
