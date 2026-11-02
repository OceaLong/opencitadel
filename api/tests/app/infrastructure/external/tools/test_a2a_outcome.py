"""A sent A2A request succeeds only with correlated completion evidence."""

import json
from datetime import timedelta

import httpx
import pytest

from app.infrastructure.external.tools import a2a_client
from app.infrastructure.external.tools.a2a_client import A2AClientManager

MESSAGE = {
    "kind": "message",
    "messageId": "message-1",
    "role": "agent",
    "parts": [{"kind": "text", "text": "completed"}],
}


def task(state, *, task_id="task-1"):
    return {"kind": "task", "id": task_id, "status": {"state": state}}


def status(state="completed", *, final=True, task_id="task-1"):
    return {
        "kind": "status-update",
        "taskId": task_id,
        "status": {"state": state},
        "final": final,
    }


def envelope(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def sse(events):
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events)


async def invoke(response, *, streaming=False, url="https://93.184.216.34/a2a"):
    manager = A2AClientManager(
        connect_timeout=timedelta(seconds=1), tool_timeout=timedelta(seconds=5)
    )
    manager._agent_cards = {"remote": {"url": url, "capabilities": {"streaming": streaming}}}
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["method"] == ("message/stream" if streaming else "message/send")
        return response(body["id"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        manager._httpx_client = client
        result = await manager.invoke("remote", "perform operation")
    assert len(requests) <= 1
    return result, requests


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [MESSAGE, {"message": MESSAGE}, task("completed")])
async def test_send_requires_correlated_completed_result(receipt):
    result, requests = await invoke(
        lambda identity: httpx.Response(200, json=envelope(identity, receipt))
    )
    assert result.success is True
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        task("submitted"),
        task("working"),
        task("input-required"),
        task("auth-required"),
        task("failed"),
        task("canceled"),
        task("rejected"),
        {"kind": "text", "text": "apparently done"},
        status(),
        {"kind": "message"},
        {**MESSAGE, "parts": [{"kind": []}]},
        {"kind": [], "status": {"state": "completed"}},
        {"kind": {}, "status": {"state": "completed"}},
        {},
    ],
)
async def test_send_nonterminal_or_invalid_receipt_cannot_confirm_effect(receipt):
    result, requests = await invoke(
        lambda identity: httpx.Response(200, json=envelope(identity, receipt))
    )
    assert result.success is False
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_response",
    [
        lambda identity: httpx.Response(200, json=envelope("other-request", MESSAGE)),
        lambda identity: httpx.Response(200, json={"id": identity, "result": MESSAGE}),
        lambda identity: httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": identity, "error": {"code": -32000, "message": "failed"}},
        ),
        lambda identity: httpx.Response(200, json={**envelope(identity, MESSAGE), "error": None}),
        lambda identity: httpx.Response(200, json=[]),
        lambda identity: httpx.Response(200, text="not json"),
        lambda identity: httpx.Response(503),
    ],
)
async def test_send_rejects_invalid_json_rpc_without_resending(make_response):
    result, requests = await invoke(make_response)
    assert result.success is False
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipts",
    [
        [task("working"), status()],
        [task("working"), task("completed")],
        [MESSAGE],
        [{"kind": "text", "text": "partial"}, {"message": MESSAGE}],
        [
            {
                "kind": "artifact-update",
                "taskId": "task-1",
                "artifact": {"parts": [{"kind": "text", "text": "output"}]},
                "lastChunk": True,
            },
            status(),
        ],
    ],
)
async def test_stream_succeeds_only_after_completion(receipts):
    result, requests = await invoke(
        lambda identity: httpx.Response(
            200,
            text=sse([envelope(identity, receipt) for receipt in receipts]),
            headers={"content-type": "text/event-stream"},
        ),
        streaming=True,
    )
    assert result.success is True
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipts",
    [
        [],
        [task("working")],
        [status(final=False)],
        [status("input-required")],
        [status("failed")],
        [{"kind": [], "status": {"state": "completed"}}],
        [{"kind": "task", "id": "task-1", "status": {"state": []}}],
        [{"kind": "text", "text": "unfinished text"}],
        [{"kind": "artifact-update", "taskId": "task-1", "artifact": {}, "lastChunk": True}],
        [task("working"), status(task_id="other-task")],
        [status(), task("working")],
        [status("failed"), status()],
    ],
)
async def test_stream_incomplete_conflicting_or_failed_receipt_never_confirms_effect(receipts):
    result, requests = await invoke(
        lambda identity: httpx.Response(
            200,
            text=sse([envelope(identity, receipt) for receipt in receipts]),
            headers={"content-type": "text/event-stream"},
        ),
        streaming=True,
    )
    assert result.success is False
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        lambda identity: "data: not-json\n\n",
        lambda identity: "unfinished plain text\n",
        lambda identity: "data: [DONE]\n\n",
        lambda identity: sse([envelope("other-request", task("completed"))]),
        lambda identity: sse([{"jsonrpc": "2.0", "id": identity, "error": {"code": -32000}}]),
        lambda identity: sse(
            [
                envelope(identity, task("completed")),
                {"jsonrpc": "2.0", "id": identity, "error": {"code": -32000}},
            ]
        ),
    ],
)
async def test_stream_never_converts_malformed_or_error_data_to_success(body):
    result, requests = await invoke(
        lambda identity: httpx.Response(
            200, text=body(identity), headers={"content-type": "text/event-stream"}
        ),
        streaming=True,
    )
    assert result.success is False
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_stream_accepts_sse_metadata_comments_and_multiline_json():
    def response(identity):
        data = json.dumps(envelope(identity, task("completed")), indent=2)
        body = (
            ": heartbeat\nevent: message\nid: 1\nretry: 1000\n"
            + "".join("data: " + line + "\n" for line in data.splitlines())
            + "\ndata: [DONE]\n\n"
        )
        return httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream; charset=utf-8"}
        )

    result, requests = await invoke(response, streaming=True)
    assert result.success is True
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_response_size_limit_remains_enforced(monkeypatch, streaming):
    monkeypatch.setattr(a2a_client, "_MAX_A2A_RESPONSE_BYTES", 40)
    result, requests = await invoke(
        lambda identity: httpx.Response(
            200,
            text=sse([envelope(identity, task("completed"))])
            if streaming
            else json.dumps(envelope(identity, MESSAGE)),
            headers={"content-type": "text/event-stream" if streaming else "application/json"},
        ),
        streaming=streaming,
    )
    assert result.success is False
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_ssrf_rejection_still_prevents_transport():
    result, requests = await invoke(
        lambda identity: httpx.Response(200, json=envelope(identity, MESSAGE)),
        url="http://127.0.0.1/a2a",
    )
    assert result.success is False
    assert requests == []
