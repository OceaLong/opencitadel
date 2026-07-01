#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prometheus metrics for browser automation (vision fallback, etc.)."""
try:
    from prometheus_client import Counter, Histogram

    BROWSER_VISION_FALLBACK_TOTAL = Counter(
        "browser_vision_fallback_total",
        "Browser click attempts using vision grounding fallback",
        ["outcome"],
    )
    BROWSER_VISION_FALLBACK_LATENCY = Histogram(
        "browser_vision_fallback_seconds",
        "Latency of vision grounding fallback click path",
        buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    )
except Exception:  # pragma: no cover - optional prometheus
    BROWSER_VISION_FALLBACK_TOTAL = None
    BROWSER_VISION_FALLBACK_LATENCY = None


def record_vision_fallback(outcome: str, elapsed_seconds: float = 0.0) -> None:
    if BROWSER_VISION_FALLBACK_TOTAL is not None:
        BROWSER_VISION_FALLBACK_TOTAL.labels(outcome=outcome).inc()
    if BROWSER_VISION_FALLBACK_LATENCY is not None and elapsed_seconds > 0:
        BROWSER_VISION_FALLBACK_LATENCY.observe(elapsed_seconds)
