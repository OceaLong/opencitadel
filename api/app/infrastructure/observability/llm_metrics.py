#!/usr/bin/env python
# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class LLMMetricsSnapshot:
    multimodal_request_total: int = 0
    multimodal_fallback_total: int = 0
    multimodal_fallback_by_reason: Dict[str, int] = field(default_factory=dict)
    multimodal_image_bytes_total: int = 0
    multimodal_image_count: int = 0

    @property
    def multimodal_image_bytes_avg(self) -> float:
        if self.multimodal_image_count == 0:
            return 0.0
        return self.multimodal_image_bytes_total / self.multimodal_image_count


_metrics = LLMMetricsSnapshot()


def record_multimodal_request(image_bytes: int = 0, image_count: int = 0) -> None:
    _metrics.multimodal_request_total += 1
    _metrics.multimodal_image_bytes_total += image_bytes
    _metrics.multimodal_image_count += image_count


def record_multimodal_fallback(reason: str) -> None:
    _metrics.multimodal_fallback_total += 1
    _metrics.multimodal_fallback_by_reason[reason] = (
        _metrics.multimodal_fallback_by_reason.get(reason, 0) + 1
    )


def get_llm_metrics_snapshot() -> LLMMetricsSnapshot:
    return _metrics
