"""Broker-owned fixed lifecycle operations and confined sandbox transports."""

import asyncio
import base64
import hashlib
import hmac
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.domain.evaluation.configuration import digest
from app.infrastructure.adapters.evaluation_sandbox import (
    LeaseBrowser,
    LeaseControlTransport,
    LeaseTargetTransport,
)
from app.infrastructure.evaluation.environment_inventory import build_environment_registry


class EvaluationBroker:
    def __init__(self, settings):
        self.registry = build_environment_registry(settings, broker=False)
        self.token = settings.sandbox_broker_token
        self.journal = Path(settings.evaluation_broker_journal_path)
        self.lock = asyncio.Lock()

    def signature(self, lease, versions, resources):
        encoded = json.dumps(
            {
                "lease": str(lease.id),
                "slot": lease.case_slot.model_dump(mode="json"),
                "generation": lease.generation,
                "version": str(lease.environment_version),
                "versions": {
                    key: value for key, value in versions.items() if key != "broker_proof"
                },
                "resources": resources,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hmac.new(self.token.encode(), encoded.encode(), hashlib.sha256).hexdigest()

    def trusted_lease(self, lease):
        supplied = lease.actual_versions.get("broker_proof", "")
        if not isinstance(supplied, str) or not hmac.compare_digest(
            supplied, self.signature(lease, lease.actual_versions, lease.resources)
        ):
            raise ValueError("environment_broker_lease_proof_invalid")
        adapter = self.registry.adapters.get(lease.actual_versions.get("adapter_revision"))
        if adapter is None:
            raise ValueError("environment_broker_adapter_unavailable")
        return adapter

    def targets(self, refs):
        result = []
        for ref in refs:
            ref = ref.model_dump(mode="json") if hasattr(ref, "model_dump") else ref
            target = self.registry.targets.get(str(ref["id"]))
            if target is None or target.revision != ref["revision"] or not target.enabled:
                raise ValueError("environment_broker_target_unavailable")
            result.append(target)
        return tuple(result)

    async def lifecycle(self, request):
        lease, operation, version = request.lease, request.operation, request.version
        targets = self.targets(version.allowed_targets)
        adapter = self.registry.resolve(version, targets)
        if version.credential_refs:
            # Local E04 adapter has no credential-bearing fixture transport.
            for ref in version.credential_refs:
                credential = self.registry.credentials.get(str(ref.id))
                if credential is None or credential.revision != ref.revision:
                    raise ValueError("environment_broker_credential_unavailable")
        identity = f"{lease.case_slot.workspace}:{lease.id}:{lease.generation}:{operation.id}:{operation.claim_generation}"
        fingerprint = digest(request.model_dump(mode="json"))
        binding_id = f"{lease.id}:{lease.generation}"
        binding = digest(
            {
                "slot": lease.case_slot.model_dump(mode="json"),
                "version": version.model_dump(mode="json"),
            }
        )
        # Serialize only fixed lifecycle actions; tool traffic uses separate requests.
        async with self.lock:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.journal) as db:
                # This records broker provenance, never authoritative PG lease state.
                db.execute(
                    "CREATE TABLE IF NOT EXISTS bindings(identity TEXT PRIMARY KEY,fingerprint TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS operations(identity TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result TEXT)"
                )
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute(
                    "SELECT fingerprint FROM bindings WHERE identity=?", (binding_id,)
                ).fetchone()
                if previous and previous[0] != binding:
                    raise ValueError("environment_broker_binding_conflict")
                if previous is None:
                    if operation.phase != "prepare" or lease.resources or lease.actual_versions:
                        raise ValueError("environment_broker_binding_missing")
                    db.execute("INSERT INTO bindings VALUES(?,?)", (binding_id, binding))
                row = db.execute(
                    "SELECT fingerprint,result FROM operations WHERE identity=?", (identity,)
                ).fetchone()
                if row:
                    if row[0] != fingerprint:
                        raise ValueError("environment_broker_operation_conflict")
                    if row[1] is None:
                        raise ValueError("environment_broker_operation_unknown")
                    return json.loads(row[1])
                db.execute("INSERT INTO operations VALUES(?,?,NULL)", (identity, fingerprint))
            action = {
                "prepare": adapter.prepare,
                "reset": adapter.reset,
                "verify_ready": adapter.verify,
                "cleanup": adapter.cleanup,
                "verify_clean": adapter.verify,
            }[operation.phase]
            result = await action(lease, operation, version, targets)
            if "actual_versions" in result:
                result["actual_versions"]["credential_revisions"] = [
                    ref.model_dump(mode="json") for ref in version.credential_refs
                ]
                result["actual_versions"]["broker_proof"] = self.signature(
                    lease, result["actual_versions"], result.get("resources", lease.resources)
                )
            with sqlite3.connect(self.journal) as db:
                db.execute(
                    "UPDATE operations SET result=? WHERE identity=? AND fingerprint=?",
                    (json.dumps(result), identity, fingerprint),
                )
            return result

    async def checked(self, lease):
        adapter = self.trusted_lease(lease)
        await adapter.check_runtime(lease)
        return adapter

    async def case(self, lease):
        return {"id": await (await self.checked(lease)).case(lease)}

    async def check(self, lease):
        await self.checked(lease)
        return {"verified": True}

    async def control(self, request):
        path = urlsplit(request.path)
        if (
            path.scheme
            or path.netloc
            or path.fragment
            or path.path
            not in {
                "/api/shell/exec-command",
                "/api/shell/read-shell-output",
                "/api/shell/write-shell-input",
                "/api/shell/wait-process",
                "/api/shell/kill-process",
                "/api/file/read-file",
                "/api/file/write-file",
                "/api/file/check-file-exists",
            }
            or request.method != "POST"
            or path.query
            or ".." in path.path
        ):
            raise ValueError("environment_control_path_forbidden")
        adapter = await self.checked(request.lease)
        body = base64.b64decode(request.body, validate=True)
        if len(body) > 20 * 1024 * 1024:
            raise ValueError("environment_control_payload_too_large")
        headers = {"Content-Type": "application/json"}
        transport = LeaseControlTransport(
            adapter, request.lease, adapter.access_token(request.lease)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.request(
                request.method,
                "http://127.0.0.1:8080" + request.path,
                content=body,
                headers=headers,
            )
        return {
            "status": response.status_code,
            "body": base64.b64encode(response.content).decode(),
            "headers": dict(response.headers),
        }

    async def browser(self, request):
        adapter = await self.checked(request.lease)
        targets = self.targets(request.lease.actual_versions["target_revisions"])
        result = await LeaseBrowser(
            adapter, request.lease, [target.endpoint for target in targets]
        ).navigate(request.url)
        return result.model_dump(mode="json")

    async def target(self, request):
        adapter = await self.checked(request.lease)
        targets = self.targets(request.lease.actual_versions["target_revisions"])
        target = next((target for target in targets if str(target.id) == request.target_id), None)
        if target is None or target.kind not in {"mcp", "a2a"}:
            raise ValueError("environment_target_forbidden")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if target.kind == "mcp":
            headers["MCP-Protocol-Version"] = "2025-03-26"
        for ref in request.lease.actual_versions.get("credential_revisions", []):
            credential = self.registry.credentials.get(str(ref["id"]))
            if (
                credential is None
                or not credential.enabled
                or credential.revision != ref["revision"]
            ):
                raise ValueError("environment_broker_credential_unavailable")
            if credential.target.id == target.id:
                resolver = self.registry.credential_resolvers[credential.resolver]
                headers.update(await resolver.resolve(None, None, target, credential))
        transport = LeaseTargetTransport(adapter, request.lease, target.endpoint, target.id)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post(
                target.endpoint,
                content=base64.b64decode(request.body, validate=True),
                headers=headers,
            )
        return {
            "status": response.status_code,
            "body": base64.b64encode(response.content).decode(),
            "headers": dict(response.headers),
        }
