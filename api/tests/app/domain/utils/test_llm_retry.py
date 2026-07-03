#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models import error_codes as EC
from app.domain.services.agents.retry_budget import RetryBudgetExceeded
from app.domain.utils.llm_retry import classify_llm_error_code


def test_retry_budget_exceeded_maps_to_task_infra_failed():
    exc = RetryBudgetExceeded("LLM重试预算耗时已耗尽: reason=structured_validation_retry")
    assert classify_llm_error_code(exc) == EC.TASK_INFRA_FAILED


def test_retry_budget_message_maps_to_task_infra_failed():
    exc = RuntimeError("LLM重试预算调用次数已耗尽: reason=agent_invoke_retry")
    assert classify_llm_error_code(exc) == EC.TASK_INFRA_FAILED
