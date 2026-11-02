"""Shared tokenizer for Chinese-friendly PostgreSQL simple tsvector."""

import re

_SPACE_RE = re.compile(r"\s+")
_QUERY_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "its",
        "me",
        "of",
        "on",
        "or",
        "please",
        "should",
        "that",
        "the",
        "these",
        "this",
        "those",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "with",
        "would",
        "的",
        "了",
        "是",
        "在",
        "和",
        "与",
        "及",
        "有",
        "吗",
        "呢",
        "什么",
        "如何",
        "怎样",
        "怎么",
        "为何",
        "是否",
        "请",
        "请问",
        "关于",
    ]
)


def segment_for_bm25(text: str) -> str:
    if not text:
        return ""
    # 延迟导入:jieba 是 worker 专用重库,api 进程不装
    import jieba

    tokens = [token.strip() for token in jieba.cut(text) if token.strip()]
    return _SPACE_RE.sub(" ", " ".join(tokens)).strip()


def segment_query_for_bm25(text: str) -> str:
    """Drop question filler without changing the persisted simple-index lexemes."""
    return " ".join(
        token
        for token in segment_for_bm25(text).split()
        if token.casefold() not in _QUERY_STOPWORDS and any(char.isalnum() for char in token)
    )
