#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.infrastructure.observability.llm_metrics import (
    get_llm_metrics_snapshot,
    record_multimodal_fallback,
    record_multimodal_request,
)


def test_llm_metrics_snapshot():
    record_multimodal_request(image_bytes=100, image_count=1)
    record_multimodal_fallback("connection")
    snapshot = get_llm_metrics_snapshot()
    assert snapshot.multimodal_request_total >= 1
    assert snapshot.multimodal_fallback_total >= 1
    assert snapshot.multimodal_fallback_by_reason.get("connection", 0) >= 1
