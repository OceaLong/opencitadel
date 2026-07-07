#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.services.agents.token_accountant import TokenAccountant
from tests.app.domain.services.agents.conftest import agent_test_observability_port


def _uow_factory(*, model_exists: bool = True, save_raises: Exception | None = None):
    mock_uow = MagicMock()
    mock_uow.session.get_metadata = AsyncMock(return_value=None)
    mock_uow.llm_model.get_by_id = AsyncMock(
        return_value=MagicMock(input_price_per_million=0.0, output_price_per_million=0.0)
        if model_exists
        else None,
    )
    if save_raises is not None:
        mock_uow.llm_token_usage.save_many = AsyncMock(side_effect=save_raises)
    else:
        mock_uow.llm_token_usage.save_many = AsyncMock()

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=mock_ctx), mock_uow


def _accountant(uow_factory):
    return TokenAccountant(
        uow_factory=uow_factory,
        session_id="session-1",
        agent_name="doc_qa",
        model_name="qwen-test",
        model_id="missing-model-id",
        observability_port=agent_test_observability_port(),
    )


@pytest.mark.asyncio
async def test_flush_swallows_integrity_error():
    factory, mock_uow = _uow_factory(save_raises=IntegrityError("fk", {}, Exception()))
    accountant = _accountant(factory)
    accountant._pending_records.append(
        MagicMock(model_id="missing-model-id", owner_user_id=None, team_id=None),
    )

    await accountant.flush()

    mock_uow.llm_token_usage.save_many.assert_awaited_once()
    assert accountant._pending_records == []


@pytest.mark.asyncio
async def test_record_clears_missing_model_id():
    factory, _mock_uow = _uow_factory(model_exists=False)
    accountant = _accountant(factory)

    await accountant.record({"prompt_tokens": 10, "completion_tokens": 5}, "default")

    assert len(accountant._pending_records) == 1
    assert accountant._pending_records[0].model_id is None


def test_sync_model_updates_active_model():
    factory, _ = _uow_factory()
    accountant = _accountant(factory)

    accountant.sync_model("fallback-id", "fallback-model")

    assert accountant._model_id == "fallback-id"
    assert accountant._model_name == "fallback-model"
    assert accountant._model_price_per_million is None


def test_sync_model_noop_when_unchanged():
    factory, _ = _uow_factory()
    accountant = _accountant(factory)
    accountant._model_price_per_million = (1.0, 2.0)

    accountant.sync_model("missing-model-id", "qwen-test")

    assert accountant._model_price_per_million == (1.0, 2.0)
