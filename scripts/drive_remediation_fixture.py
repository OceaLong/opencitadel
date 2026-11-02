"""Deterministic, LLM-free harness for fixture 21's remediation flow.

Drives a *real* Ops Actuator MCP server (streamable-http, no mocks) against
the disposable kind cluster's ``fixture-remediation-crashloop`` Deployment
and cross-checks every step against real cluster state read back through
``kubectl`` and the real Ops Collector K8s adapter. No LLM, no fakes: every
assertion below is either an MCP tool envelope returned by a live actuator
pod or a ``kubectl``/Collector read of the live object.

Invoked by scripts/run-patrol-fixtures.sh only when
PATROL_RUN_REMEDIATION_FIXTURE=true, immediately after apply-fixture.sh has
applied demo/patrol/fixtures/21-remediation-crashloop/setup.yaml and
confirmed (via its own restartCount>=11 poll) that the fixture is actually
crash-looping. The actuator itself is expected to already be deployed (into
the `opencitadel` namespace, kept separate from `opencitadel-patrol-demo` so
it never perturbs run-patrol-fixtures.sh's baseline_signature) by the caller
before this script runs.

Steps (all deterministic, no waiting on non-deterministic LLM output):
  1. Collector observes the pre-remediation fail state
     (unavailable_replicas>=1, restart_count_1h>=11) -- proves the Collector's
     own K8s adapter (not just apply-fixture.sh's raw kubectl poll) sees the
     fault before any write happens.
  2. kubectl port-forward to the actuator Service (bypasses any
     NetworkPolicy: kubectl port-forward tunnels through the kubelet into
     the pod's network namespace directly, it never traverses the CNI's
     pod-to-pod path that NetworkPolicy governs -- so the actuator's
     production-shaped ingress NetworkPolicy is left untouched).
  3. get_capabilities: assert overall_capability_hash is present and all
     three write actions are registered.
  4. get_target freezes the live Deployment UID. restart_workload receives a
     platform UUID, rem:{id} key and canonical payload digest. Check the durable
     receipt and operation/digest annotations against the real Deployment.
  5. Explicitly replay the same approved identity: the retained receipt must be
     returned without a second rollout. Query get_operation and check the same
     receipt; target annotations are only supporting evidence.
  6. Apply healthy.yaml (models "redeploy a fixed image" -- restart_workload
     alone cannot repair this fixture's literal crash loop, see setup.yaml's
     comment) and wait for the rollout to finish.
  7. Collector observes the post-heal pass state (unavailable_replicas==0,
     restart_count_1h==0).

Exit code 0 on success. Any failed assertion raises HarnessError, which is
printed together with kubectl/actuator-log diagnostics before exiting
non-zero.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import select
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "opencitadel-patrol-demo"
DEPLOYMENT = "fixture-remediation-crashloop"
ACTUATOR_NAMESPACE = os.environ.get("OPS_ACTUATOR_NAMESPACE", "opencitadel")
ACTUATOR_SERVICE = os.environ.get("OPS_ACTUATOR_SERVICE", "opencitadel-ops-actuator")
ACTUATOR_PORT = int(os.environ.get("OPS_ACTUATOR_PORT", "8091"))
LOCAL_PORT = int(os.environ.get("OPS_ACTUATOR_LOCAL_PORT", "18091"))
OPERATION_ANNOTATION = "opencitadel.io/operation-id"
PAYLOAD_ANNOTATION = "opencitadel.io/payload-digest"
RESTARTED_AT_ANNOTATION = "opencitadel.io/restarted-at"
HEALTHY_MANIFEST = (
    ROOT / "demo" / "patrol" / "fixtures" / "21-remediation-crashloop" / "healthy.yaml"
)
WRITE_TOOLS = {"restart_workload", "scale_workload", "rollback_workload"}
REQUIRED_TOOLS = WRITE_TOOLS | {"get_target", "get_operation"}


class HarnessError(RuntimeError):
    """A deterministic assertion failed; the message already carries diagnostics."""


def context() -> str:
    ctx = os.environ.get("PATROL_DEMO_CONTEXT", "")
    if not ctx or not ctx.startswith("kind-opencitadel-patrol-") or "prod" in ctx.lower():
        raise HarnessError(f"refusing non-disposable Kubernetes context: {ctx!r}")
    return ctx


def kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["kubectl", "--context", context(), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def kubectl_json(*args: str) -> dict:
    result = kubectl(*args, "-o", "json")
    return json.loads(result.stdout)


def get_deployment(namespace: str, name: str) -> dict:
    return kubectl_json("-n", namespace, "get", "deployment", name)


def object_annotations(deployment: dict) -> dict:
    """Read operation/digest markers on the Deployment, separate from pod metadata."""
    return deployment["metadata"].get("annotations") or {}


def pod_template_annotations(deployment: dict) -> dict:
    """spec.template.metadata.annotations -- the Pod template's own map.

    This is where the same restart() call patches RESTARTED_AT_ANNOTATION
    (opencitadel.io/restarted-at), via the patch body's
    ``"spec": {"template": {"metadata": {"annotations": {...}}}}`` key.
    This is deliberately distinct from object_annotations(): the idempotency
    key exists only on the workload object's own metadata.
    """
    return deployment["spec"]["template"]["metadata"].get("annotations") or {}


def deployment_generation(deployment: dict) -> int:
    return int(deployment["metadata"]["generation"])


class PortForward:
    """Owns a `kubectl port-forward` subprocess for the lifetime of a `with` block."""

    def __init__(self, namespace: str, service: str, local_port: int, remote_port: int) -> None:
        self._cmd = [
            "kubectl",
            "--context",
            context(),
            "-n",
            namespace,
            "port-forward",
            "--address",
            "127.0.0.1",
            f"service/{service}",
            f"{local_port}:{remote_port}",
        ]
        self._process: subprocess.Popen | None = None

    def __enter__(self) -> Self:
        self._process = subprocess.Popen(
            self._cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._wait_ready(timeout=30)
        return self

    def _wait_ready(self, timeout: float) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        deadline = time.monotonic() + timeout
        buffer = ""
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                buffer += self._process.stdout.read()
                raise HarnessError(
                    f"kubectl port-forward exited early (code={self._process.returncode}); output={buffer}"
                )
            ready, _, _ = select.select([self._process.stdout], [], [], 0.5)
            if not ready:
                continue
            line = self._process.stdout.readline()
            if not line:
                continue
            buffer += line
            print(f"[port-forward] {line.rstrip()}", file=sys.stderr)
            if "Forwarding from" in line:
                return
        raise HarnessError(
            f"kubectl port-forward did not become ready before timeout; output so far: {buffer}"
        )

    def __exit__(self, *exc_info: object) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=10)


def _extract_tool_result(result: Any, tool_name: str) -> dict:
    if getattr(result, "isError", False):
        raise HarnessError(f"MCP tool {tool_name} returned a protocol-level error: {result!r}")
    structured = getattr(result, "structuredContent", None)
    if structured:
        return dict(structured)
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    raise HarnessError(f"MCP tool {tool_name} returned no parsable content: {result!r}")


async def _call_tool_async(url: str, tool_name: str, arguments: dict, attempts: int = 3) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    token = os.environ.get("PATROL_ACTUATOR_TOKEN", "")
    if len(token) < 32:
        raise HarnessError("PATROL_ACTUATOR_TOKEN must contain at least 32 characters")
    # A timeout may have applied. Only the explicit replay step may reuse a key.
    if tool_name in WRITE_TOOLS:
        attempts = min(attempts, 1)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with (
                streamablehttp_client(url=url, headers={"Authorization": f"Bearer {token}"}) as (
                    read_stream,
                    write_stream,
                    _get_session_id,
                ),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return _extract_tool_result(result, tool_name)
        except HarnessError:
            raise
        except (ExceptionGroup, OSError, RuntimeError, ValueError) as exc:
            last_exc = exc
            print(
                f"[mcp] attempt {attempt}/{attempts} for {tool_name} failed: {exc!r}",
                file=sys.stderr,
            )
            if attempt < attempts:
                await asyncio.sleep(1.5 * attempt)
    raise HarnessError(f"MCP call {tool_name} failed after {attempts} attempts: {last_exc!r}")


def call_tool(url: str, tool_name: str, arguments: dict) -> dict:
    return asyncio.run(_call_tool_async(url, tool_name, arguments))


async def _collector_eventually(
    namespace: str, predicate: Callable[[dict], bool], description: str, timeout_seconds: int = 90
) -> dict:
    from opencitadel_ops_collector.collector import OpsCollector
    from opencitadel_ops_collector.config import CollectorSettings

    collector = OpsCollector(
        CollectorSettings(target_ref="patrol-demo-remediation", allowed_namespaces=[namespace])
    )
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict | None = None
    while asyncio.get_running_loop().time() < deadline:
        last = await collector.k8s_workload_summary(namespace, [], 3600)
        if last["status"] == "ok" and predicate(last["data"]):
            return last
        await asyncio.sleep(2)
    raise HarnessError(f"collector never observed {description}; last observation={last}")


def collector_eventually(*args: Any, **kwargs: Any) -> dict:
    return asyncio.run(_collector_eventually(*args, **kwargs))


def dump_diagnostics(ctx: str) -> None:
    print("\n--- diagnostics ---", file=sys.stderr)
    checks = [
        ["-n", NAMESPACE, "get", "deployment", DEPLOYMENT, "-o", "yaml"],
        ["-n", NAMESPACE, "get", "pods", "-o", "wide"],
        ["-n", ACTUATOR_NAMESPACE, "get", "pods", "-o", "wide"],
        ["-n", ACTUATOR_NAMESPACE, "logs", f"deployment/{ACTUATOR_SERVICE}", "--tail=200"],
    ]
    for args in checks:
        try:
            result = kubectl(*args, check=False)
            print(f"$ kubectl --context {ctx} {' '.join(args)}", file=sys.stderr)
            print(result.stdout, file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
        except Exception as diag_exc:  # noqa: BLE001 - best-effort diagnostics only
            print(f"(diagnostic command {args} failed: {diag_exc!r})", file=sys.stderr)


def main() -> int:
    ctx = context()
    print(
        f"[drive_remediation_fixture] context={ctx} namespace={NAMESPACE} deployment={DEPLOYMENT} "
        f"actuator={ACTUATOR_NAMESPACE}/{ACTUATOR_SERVICE}:{ACTUATOR_PORT}",
        file=sys.stderr,
    )
    try:
        print("[1/7] pre-remediation collector fail-state check", file=sys.stderr)
        collector_eventually(
            NAMESPACE,
            lambda data: data["unavailable_replicas"] >= 1 and data["restart_count_1h"] >= 11,
            "fixture-remediation-crashloop failing (unavailable_replicas>=1, restart_count_1h>=11)",
        )

        actuator_url = f"http://127.0.0.1:{LOCAL_PORT}/mcp"
        with PortForward(ACTUATOR_NAMESPACE, ACTUATOR_SERVICE, LOCAL_PORT, ACTUATOR_PORT):
            print("[2/7] get_capabilities", file=sys.stderr)
            manifest = call_tool(actuator_url, "get_capabilities", {})
            if not manifest.get("overall_capability_hash"):
                raise HarnessError(
                    f"get_capabilities response missing overall_capability_hash: {manifest}"
                )
            enabled = set(manifest.get("enabled_tools", []))
            missing_tools = REQUIRED_TOOLS - enabled
            if missing_tools:
                raise HarnessError(
                    f"get_capabilities missing required tools {missing_tools}: {manifest}"
                )

            target = call_tool(
                actuator_url,
                "get_target",
                {
                    "namespace": NAMESPACE,
                    "workload": DEPLOYMENT,
                    "kind": "deployment",
                },
            )
            deployment_before = get_deployment(NAMESPACE, DEPLOYMENT)
            if (
                target.get("uid") != deployment_before["metadata"]["uid"]
                or target.get("target_ref") != manifest["target_ref"]
            ):
                raise HarnessError("get_target identity does not match the live fixture")
            operation_id = str(uuid.uuid4())
            idempotency_key = f"rem:{operation_id}"
            payload = {
                "action": "restart_workload",
                "target_ref": manifest["target_ref"],
                "namespace": NAMESPACE,
                "workload": DEPLOYMENT,
                "kind": "deployment",
                "target_uid": target["uid"],
                "parameters": {},
            }
            payload_digest = hashlib.sha256(
                json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            arguments = {
                "namespace": NAMESPACE,
                "workload": DEPLOYMENT,
                "kind": "deployment",
                "idempotency_key": idempotency_key,
                "operation_id": operation_id,
                "payload_digest": payload_digest,
                "target_uid": target["uid"],
            }
            print(f"[3/7] restart_workload (operation_id={operation_id})", file=sys.stderr)
            envelope_1 = call_tool(actuator_url, "restart_workload", arguments)
            if envelope_1.get("action_outcome") != "applied":
                raise HarnessError(f"first restart_workload call did not apply: {envelope_1}")

            if envelope_1.get("state") != "applied" or not envelope_1.get("receipt"):
                raise HarnessError("Actuator did not retain an applied receipt")
            deployment_1 = get_deployment(NAMESPACE, DEPLOYMENT)
            object_annotations_1 = object_annotations(deployment_1)
            if (
                object_annotations_1.get(OPERATION_ANNOTATION) != operation_id
                or object_annotations_1.get(PAYLOAD_ANNOTATION) != payload_digest
            ):
                raise HarnessError("operation/digest annotations do not bind the applied operation")
            generation_1 = deployment_generation(deployment_1)
            restarted_at_1 = pod_template_annotations(deployment_1).get(RESTARTED_AT_ANNOTATION)

            print("[4/7] replay restart_workload with the same idempotency_key", file=sys.stderr)
            envelope_2 = call_tool(actuator_url, "restart_workload", arguments)
            if envelope_2.get("action_outcome") != "skipped_idempotent":
                raise HarnessError(f"replayed restart_workload was not a no-op: {envelope_2}")

            deployment_2 = get_deployment(NAMESPACE, DEPLOYMENT)
            object_annotations_2 = object_annotations(deployment_2)
            generation_2 = deployment_generation(deployment_2)
            restarted_at_2 = pod_template_annotations(deployment_2).get(RESTARTED_AT_ANNOTATION)
            if (
                object_annotations_2.get(OPERATION_ANNOTATION) != operation_id
                or object_annotations_2.get(PAYLOAD_ANNOTATION) != payload_digest
            ):
                raise HarnessError("operation identity changed on explicit idempotent replay")
            queried = call_tool(actuator_url, "get_operation", {"operation_id": operation_id})
            if (
                queried.get("state") != "applied"
                or queried.get("receipt") != envelope_1["receipt"]
                or envelope_2.get("receipt") != envelope_1["receipt"]
            ):
                raise HarnessError(
                    "get_operation/replay did not recover the original durable receipt"
                )
            if restarted_at_2 != restarted_at_1:
                raise HarnessError(
                    "opencitadel.io/restarted-at annotation changed on idempotent replay -- "
                    f"a second rollout was triggered: {restarted_at_1!r} -> {restarted_at_2!r}"
                )
            if generation_2 != generation_1:
                raise HarnessError(
                    f"Deployment metadata.generation changed on idempotent replay: {generation_1} -> {generation_2}"
                )

        print("[5/7] apply healthy.yaml (models a redeploy of a fixed image)", file=sys.stderr)
        kubectl("apply", "-f", str(HEALTHY_MANIFEST))
        print("[6/7] wait for the healthy rollout", file=sys.stderr)
        kubectl("-n", NAMESPACE, "rollout", "status", f"deployment/{DEPLOYMENT}", "--timeout=180s")

        print("[7/7] post-heal collector pass-state check", file=sys.stderr)
        collector_eventually(
            NAMESPACE,
            lambda data: data["unavailable_replicas"] == 0 and data["restart_count_1h"] == 0,
            "fixture-remediation-crashloop healthy (unavailable_replicas==0, restart_count_1h==0)",
        )
    except HarnessError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        dump_diagnostics(ctx)
        return 1
    except Exception as exc:
        print(f"\nFAILED (unexpected): {exc!r}", file=sys.stderr)
        dump_diagnostics(ctx)
        raise

    print(
        "\nOK: fixture 21 remediation verified end-to-end "
        "(fail -> restart -> idempotent replay -> heal -> recheck pass)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
