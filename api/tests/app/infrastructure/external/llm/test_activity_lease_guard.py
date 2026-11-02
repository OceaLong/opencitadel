"""Physical inference sends cannot outlive local Activity permission."""

from contextlib import asynccontextmanager

import pytest

from app.application.execution.activity_worker import ActivityLeaseLostError
from app.application.ports.inference_dispatch import (
    activity_lease_context,
    dispatch_context,
    physical_request_context,
)
from app.infrastructure.external.llm.dispatch import physical_send, physical_stream


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["sdk", "http_stream"])
@pytest.mark.parametrize("loss", ["before_admission", "during_admission"])
async def test_lease_loss_prevents_transport_and_permit_consumption(transport, loss):
    allowed = loss != "before_admission"
    counts = {"admission": 0, "consume": 0, "send": 0}

    def require_lease():
        if not allowed:
            raise ActivityLeaseLostError("lease unconfirmed")

    class Permit:
        def consume(self):
            counts["consume"] += 1
            return "physical-1"

    class Guard:
        async def before_send(self, model, payload):
            nonlocal allowed
            counts["admission"] += 1
            allowed = False
            return Permit()

    async def send():
        counts["send"] += 1
        raise AssertionError("external transport must not be invoked")

    class Client:
        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            counts["send"] += 1
            raise AssertionError("external stream must not be opened")
            yield  # pragma: no cover - marks an async context manager

    async def attempt_transport():
        if transport == "sdk":
            await physical_send(send, {}, provider="openai")
        else:
            async with physical_stream(
                Client(), "https://example.invalid", payload={}, provider="openai"
            ):
                pytest.fail("unreachable stream")

    with (
        activity_lease_context(require_lease),
        dispatch_context(Guard(), "primary"),
        physical_request_context(None, None, "embedding", "embedding-model"),
        pytest.raises(ActivityLeaseLostError),
    ):
        await attempt_transport()
    assert counts == {"admission": int(loss == "during_admission"), "consume": 0, "send": 0}


@pytest.mark.asyncio
async def test_lease_binding_is_restored_and_direct_request_is_unaffected():
    sent = []

    def lost():
        raise ActivityLeaseLostError("lost")

    async def send():
        sent.append(True)
        return {}

    with activity_lease_context(lost), pytest.raises(ActivityLeaseLostError):
        await physical_send(send, {}, provider="openai")
    await physical_send(send, {}, provider="openai")
    assert sent == [True]


@pytest.mark.asyncio
async def test_late_physical_receipt_may_still_settle_independent_usage():
    allowed = True
    receipts = []

    def require_lease():
        if not allowed:
            raise ActivityLeaseLostError("lost")

    class Guard:
        async def before_send(self, model, payload):
            return "physical-1"

        async def after_send(self, identity, usage, revision):
            receipts.append((identity, usage, revision))

    async def send():
        nonlocal allowed
        allowed = False
        return {"model": "actual-revision", "usage": {"prompt_tokens": 2, "completion_tokens": 1}}

    with activity_lease_context(require_lease), dispatch_context(Guard(), "model"):
        await physical_send(send, {}, provider="openai")
        with pytest.raises(ActivityLeaseLostError):
            await physical_send(send, {}, provider="openai")
    assert len(receipts) == 1
    assert receipts[0][0] == "physical-1"
