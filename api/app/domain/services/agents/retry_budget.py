#!/usr/bin/env python
# -*- coding: utf-8 -*-
import time
from dataclasses import dataclass


class RetryBudgetExceeded(RuntimeError):
    """Raised when a user-turn LLM retry budget is exhausted."""


@dataclass
class LLMRetryBudget:
    """Shared budget across agent retries, resilience retries, and structured repair."""

    max_calls: int
    deadline_monotonic: float
    used_calls: int = 0

    @classmethod
    def create(cls, *, max_calls: int, max_seconds: float) -> "LLMRetryBudget":
        return cls(
            max_calls=max(1, int(max_calls)),
            deadline_monotonic=time.monotonic() + max(1.0, float(max_seconds)),
        )

    def refresh_deadline(self, max_seconds: float) -> None:
        self.deadline_monotonic = time.monotonic() + max(1.0, float(max_seconds))

    def consume(self, reason: str = "llm_call", *, ignore_deadline: bool = False) -> None:
        if not ignore_deadline and time.monotonic() >= self.deadline_monotonic:
            raise RetryBudgetExceeded(f"LLM重试预算耗时已耗尽: reason={reason}")
        if self.used_calls >= self.max_calls:
            raise RetryBudgetExceeded(f"LLM重试预算调用次数已耗尽: reason={reason}")
        self.used_calls += 1

