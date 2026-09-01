"""Security coverage for the public, unauthenticated webhook entry (G1).

``POST /webhooks/{job_token}`` (``scheduling_routes.py``) is an internet-facing
entry point whose *only* authentication is an HMAC-SHA256 signature over the raw
request body, carried in the ``X-Webhook-Signature`` header and checked against a
per-job secret in ``ScheduledJobService.trigger_webhook`` /
``verify_webhook_signature``.

Two layers are exercised:

* Service layer (``ScheduledJobService.trigger_webhook``) runs the *real* HMAC
  verification against a per-job secret so the crypto path is covered: a correct
  signature admits the job, and every rejection vector (missing / wrong / body-
  tampered signature, unknown or disabled token) is refused before ``trigger_job``
  runs.
* Endpoint layer mounts only ``webhook_router`` (plus the shared exception
  handlers) so the HTTP status mapping and raw-body / header plumbing are pinned
  without depending on the full application lifespan (which needs a seeded
  runtime-policy head in the DB).

Findings recorded as behavioural assertions (NOT fixed here -- tests only):

1. No anti-replay: the signature covers only the body, and idempotency is a
   coarse TTL *bucket* keyed on the body hash. A captured ``(body, signature)``
   pair replayed in a later TTL window triggers a brand-new run. See
   ``test_valid_signature_replayed_in_later_bucket_is_accepted``.
2. Token-existence oracle: an unknown token yields 404 while a *known* token with
   a bad signature yields 401, so the two responses differ. See
   ``test_unknown_token_and_bad_signature_return_distinguishable_status``. (The
   token is 128-bit, so enumeration is infeasible; recorded as an observation.)
"""

import hashlib
import hmac
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.services.scheduled_job_service import ScheduledJobService
from app.interfaces.endpoints.scheduling_routes import webhook_router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_scheduled_job_service

_SECRET = "super-secret-webhook-key"
_TTL_SECONDS = 300


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------- #
# Service layer: real HMAC verification
# --------------------------------------------------------------------------- #


def _make_job(**overrides):
    job = SimpleNamespace(
        id="job-1",
        enabled=True,
        webhook_secret_hash="encrypted-blob",
        last_run_at=None,
        last_run_session_id=None,
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def _make_service(job, *, ttl_seconds: int = _TTL_SECONDS, secret: str | None = _SECRET):
    """Build a ScheduledJobService whose only real behaviour is HMAC checking.

    ``trigger_job`` is replaced with an AsyncMock so a successful signature does
    not drag in the full run-admission machinery; every dependency the crypto
    path does not touch is ``None``.
    """

    def _uow_factory():
        @asynccontextmanager
        async def _cm():
            yield SimpleNamespace(
                scheduled_job=SimpleNamespace(get_by_webhook_token=AsyncMock(return_value=job))
            )

        return _cm()

    secret_cipher = SimpleNamespace(decrypt_versioned=lambda _stored: secret)
    policy_reader = SimpleNamespace(
        active_operations=AsyncMock(
            return_value=SimpleNamespace(
                revision=SimpleNamespace(
                    policy=SimpleNamespace(
                        scheduler=SimpleNamespace(webhook_idempotency_ttl_seconds=ttl_seconds)
                    )
                )
            )
        )
    )
    service = ScheduledJobService(
        uow_factory=_uow_factory,
        patrol_run_service=None,
        resource_guard=None,
        resource_binding_service=None,
        run_admission_service=None,
        run_projection=None,
        policy_reader=policy_reader,
        notification_service=None,
        secret_cipher=secret_cipher,
    )
    service.trigger_job = AsyncMock(return_value="session-abc")
    return service


@pytest.mark.asyncio
async def test_valid_signature_triggers_job():
    body = b'{"event": "ping"}'
    service = _make_service(_make_job())

    session_id, error = await service.trigger_webhook(
        "job-token", body, _sign(_SECRET, body), {"event": "ping"}
    )

    assert error is None
    assert session_id == "session-abc"
    service.trigger_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_signature_is_rejected():
    body = b'{"event": "ping"}'
    service = _make_service(_make_job())

    session_id, error = await service.trigger_webhook("job-token", body, "", {})

    assert error == "unauthorized"
    assert session_id is None
    service.trigger_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_signature_is_rejected():
    body = b'{"event": "ping"}'
    service = _make_service(_make_job())

    session_id, error = await service.trigger_webhook(
        "job-token", body, _sign("attacker-guess", body), {}
    )

    assert error == "unauthorized"
    assert session_id is None
    service.trigger_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_tampered_body_breaks_signature():
    # Sign the original body, then submit a mutated body: the HMAC over the raw
    # bytes no longer matches, so the request is refused.
    original = b'{"amount": 1}'
    signature = _sign(_SECRET, original)
    tampered = b'{"amount": 1000000}'
    service = _make_service(_make_job())

    session_id, error = await service.trigger_webhook("job-token", tampered, signature, {})

    assert error == "unauthorized"
    assert session_id is None
    service.trigger_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_token_is_not_found():
    body = b"{}"
    service = _make_service(None)  # repo resolves the token to nothing

    session_id, error = await service.trigger_webhook(
        "does-not-exist", body, _sign(_SECRET, body), {}
    )

    assert error == "not_found"
    assert session_id is None
    service.trigger_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_job_is_not_found_even_with_valid_signature():
    # A disabled job must not be triggerable and reports the same "not_found"
    # code as a truly missing token (does not leak enabled/disabled state).
    body = b"{}"
    service = _make_service(_make_job(enabled=False))

    session_id, error = await service.trigger_webhook("job-token", body, _sign(_SECRET, body), {})

    assert error == "not_found"
    assert session_id is None
    service.trigger_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_undecryptable_secret_is_rejected():
    # If the stored secret cannot be decrypted, the webhook can never be
    # authenticated, so it must be refused rather than admitted.
    body = b"{}"
    service = _make_service(_make_job(), secret=None)

    session_id, error = await service.trigger_webhook("job-token", body, _sign(_SECRET, body), {})

    assert error == "unauthorized"
    assert session_id is None
    service.trigger_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotent_replay_within_same_bucket_returns_duplicate():
    # Within the same TTL window an identical body maps to the same firing_id
    # bucket; the service short-circuits to the prior session as a "duplicate"
    # instead of launching a second run.
    body = b'{"event": "ping"}'
    fired_at = datetime.fromtimestamp(
        (int(datetime.now(UTC).timestamp()) // _TTL_SECONDS) * _TTL_SECONDS, UTC
    )
    job = _make_job(last_run_at=fired_at, last_run_session_id="prior-session")
    service = _make_service(job)

    session_id, error = await service.trigger_webhook("job-token", body, _sign(_SECRET, body), {})

    assert error == "duplicate"
    assert session_id == "prior-session"
    service.trigger_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_signature_replayed_in_later_bucket_is_accepted():
    # FINDING (no anti-replay): the idempotency bucket is time-based, so a valid
    # (body, signature) captured earlier and replayed in a *later* TTL window is
    # accepted as a fresh trigger. A stale last_run_at (a different bucket) does
    # not stop the replay. Recorded as behaviour; production code unchanged.
    body = b'{"event": "ping"}'
    job = _make_job(
        last_run_at=datetime(2000, 1, 1, tzinfo=UTC),  # some far-earlier bucket
        last_run_session_id="ancient-session",
    )
    service = _make_service(job, ttl_seconds=1)

    session_id, error = await service.trigger_webhook("job-token", body, _sign(_SECRET, body), {})

    assert error is None
    assert session_id == "session-abc"
    service.trigger_job.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Endpoint layer: HTTP status mapping + raw-body / header plumbing
# --------------------------------------------------------------------------- #


@pytest.fixture
def webhook_client():
    service = AsyncMock()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(webhook_router)
    app.dependency_overrides[get_scheduled_job_service] = lambda: service
    with TestClient(app) as client:
        yield client, service


def test_endpoint_is_public_and_forwards_raw_body_and_signature(webhook_client):
    # The route carries no auth dependency: it is reachable with no bearer token
    # and no session cookie -- the HMAC is the only gate. Assert the handler
    # forwards the *raw* body bytes and the X-Webhook-Signature header verbatim,
    # since the signature is computed over exactly those bytes.
    client, service = webhook_client
    service.trigger_webhook.return_value = ("session-9", None)
    raw = b'{"event":"ping","n":1}'

    resp = client.post(
        "/webhooks/tok-1",
        content=raw,
        headers={"X-Webhook-Signature": "deadbeef", "Content-Type": "application/json"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"session_id": "session-9", "duplicate": False}
    (token, body, signature, payload), _ = service.trigger_webhook.call_args
    assert token == "tok-1"
    assert body == raw
    assert signature == "deadbeef"
    assert payload == {"event": "ping", "n": 1}


def test_endpoint_missing_signature_header_forwards_empty_string(webhook_client):
    client, service = webhook_client
    service.trigger_webhook.return_value = (None, "unauthorized")

    resp = client.post("/webhooks/tok-1", content=b"{}")

    assert resp.status_code == 401
    (_token, _body, signature, _payload), _ = service.trigger_webhook.call_args
    assert signature == ""


def test_endpoint_unauthorized_maps_to_401(webhook_client):
    client, service = webhook_client
    service.trigger_webhook.return_value = (None, "unauthorized")

    resp = client.post("/webhooks/tok-1", content=b"{}", headers={"X-Webhook-Signature": "bad"})

    assert resp.status_code == 401


def test_endpoint_not_found_maps_to_404(webhook_client):
    client, service = webhook_client
    service.trigger_webhook.return_value = (None, "not_found")

    resp = client.post("/webhooks/missing", content=b"{}", headers={"X-Webhook-Signature": "x"})

    assert resp.status_code == 404


def test_endpoint_unknown_token_and_bad_signature_return_distinguishable_status(
    webhook_client,
):
    # FINDING (token-existence oracle): a missing token -> 404 while a *valid*
    # token with a bad signature -> 401. The differing status distinguishes
    # "token exists" from "token unknown". The webhook token is 128-bit random,
    # so this is not practically exploitable, but the distinction exists.
    client, service = webhook_client

    service.trigger_webhook.return_value = (None, "not_found")
    unknown = client.post("/webhooks/unknown", content=b"{}", headers={"X-Webhook-Signature": "x"})

    service.trigger_webhook.return_value = (None, "unauthorized")
    known_bad_sig = client.post(
        "/webhooks/known", content=b"{}", headers={"X-Webhook-Signature": "wrong"}
    )

    assert unknown.status_code == 404
    assert known_bad_sig.status_code == 401
    assert unknown.status_code != known_bad_sig.status_code


def test_endpoint_duplicate_reported_in_body(webhook_client):
    client, service = webhook_client
    service.trigger_webhook.return_value = ("session-dup", "duplicate")

    resp = client.post("/webhooks/tok-1", content=b"{}", headers={"X-Webhook-Signature": "x"})

    assert resp.status_code == 200
    assert resp.json() == {"session_id": "session-dup", "duplicate": True}
