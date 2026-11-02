"""Real MCP processes and Kubernetes API; faults affect transport, never fake API state."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from opencitadel_ops_actuator.contracts import operation_payload_digest


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


class KubernetesTransportProxy:
    """Forward all traffic to a real API server; optionally withhold one PATCH reply."""

    def __init__(self, upstream, ca):
        self.upstream = upstream
        self.patches = []
        self.hold_next_patch = False
        self.applied = threading.Event()
        self.release = threading.Event()
        self.ssl = ssl.create_default_context(cafile=str(ca))
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.forward()

            def do_POST(self):
                self.forward()

            def do_PUT(self):
                self.forward()

            def do_PATCH(self):
                self.forward()

            def log_message(self, *args):
                pass

            def forward(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower() not in {"host", "connection", "accept-encoding"}
                }
                with httpx.Client(verify=proxy.ssl, timeout=30, trust_env=False) as client:
                    response = client.request(
                        self.command, proxy.upstream + self.path, content=body, headers=headers
                    )
                if self.command == "PATCH" and "/deployments/" in self.path:
                    proxy.patches.append(
                        {
                            "path": self.path,
                            "body": json.loads(body),
                            "status": response.status_code,
                            "audit_id": response.headers.get("audit-id"),
                        }
                    )
                    if proxy.hold_next_patch and response.is_success:
                        proxy.hold_next_patch = False
                        proxy.applied.set()
                        proxy.release.wait(30)
                try:
                    self.send_response(response.status_code)
                    self.send_header(
                        "Content-Type", response.headers.get("content-type", "application/json")
                    )
                    self.send_header("Content-Length", str(len(response.content)))
                    self.end_headers()
                    self.wfile.write(response.content)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class LiveActuatorFixture:
    def __init__(self, directory, case_name):
        self.directory = directory
        self.case_name = case_name
        self.context = os.environ["PATROL_DEMO_CONTEXT"]
        self.namespace = "actuator-proof-" + uuid4().hex[:12]
        self.token = secrets.token_urlsafe(48)
        self.processes = []
        self.proxy = None
        self.evidence = []

    def kubectl(self, *args, value=None):
        result = subprocess.run(
            ["kubectl", "--context", self.context, *args],
            input=None if value is None else json.dumps(value),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"isolated kubectl {args[0]} failed: {result.stderr[:500]}")
        return result.stdout

    def apply(self, value):
        return self.kubectl("apply", "-f", "-", value=value)

    def target(self):
        return json.loads(
            self.kubectl("-n", self.namespace, "get", "deployment", "target", "-o", "json")
        )

    def deployment(self):
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "target", "namespace": self.namespace},
            "spec": {
                "replicas": 0,
                "selector": {"matchLabels": {"app": "effect-proof"}},
                "template": {
                    "metadata": {"labels": {"app": "effect-proof"}},
                    "spec": {"containers": [{"name": "target", "image": "nginx:1.27-alpine"}]},
                },
            },
        }

    def journal_role(self, allowed):
        rules = (
            [{"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get", "create", "update"]}]
            if allowed
            else []
        )
        self.apply(
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": {"name": "journal", "namespace": self.namespace},
                "rules": rules,
            }
        )

    def start(self):
        self.apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": self.namespace}})
        self.apply(self.deployment())
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {"name": "actuator", "namespace": self.namespace},
            }
        )
        for name, rules in [
            (
                "target",
                [
                    {
                        "apiGroups": ["apps"],
                        "resources": ["deployments"],
                        "resourceNames": ["target"],
                        "verbs": ["get", "patch"],
                    }
                ],
            ),
            (
                "journal",
                [
                    {
                        "apiGroups": [""],
                        "resources": ["configmaps"],
                        "verbs": ["get", "create", "update"],
                    }
                ],
            ),
        ]:
            self.apply(
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "Role",
                    "metadata": {"name": name, "namespace": self.namespace},
                    "rules": rules,
                }
            )
            self.apply(
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "RoleBinding",
                    "metadata": {"name": name, "namespace": self.namespace},
                    "roleRef": {
                        "apiGroup": "rbac.authorization.k8s.io",
                        "kind": "Role",
                        "name": name,
                    },
                    "subjects": [
                        {"kind": "ServiceAccount", "name": "actuator", "namespace": self.namespace}
                    ],
                }
            )
        config = json.loads(self.kubectl("config", "view", "--minify", "--raw", "-o", "json"))
        cluster = config["clusters"][0]["cluster"]
        ca = self.directory / "ca.pem"
        ca.write_bytes(base64.b64decode(cluster["certificate-authority-data"]))
        self.proxy = KubernetesTransportProxy(cluster["server"], ca)
        credential = self.kubectl(
            "-n", self.namespace, "create", "token", "actuator", "--duration=1h"
        ).strip()
        self.kubeconfig = self.directory / "runtime-kubeconfig.json"
        self.kubeconfig.write_text(
            json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Config",
                    "clusters": [{"name": "fixture", "cluster": {"server": self.proxy.url}}],
                    "users": [{"name": "actuator", "user": {"token": credential}}],
                    "contexts": [
                        {
                            "name": self.context,
                            "context": {
                                "cluster": "fixture",
                                "user": "actuator",
                                "namespace": self.namespace,
                            },
                        }
                    ],
                    "current-context": self.context,
                }
            )
        )
        self.kubeconfig.chmod(0o600)
        self.start_process()
        self.start_process()
        return self

    def start_process(self):
        port = free_port()
        env = {
            **os.environ,
            "KUBECONFIG": str(self.kubeconfig),
            "OPS_ACTUATOR_TOKEN": self.token,
            "OPS_ACTUATOR_HOST": "127.0.0.1",
            "OPS_ACTUATOR_PORT": str(port),
            "OPS_ACTUATOR_JOURNAL_NAMESPACE": self.namespace,
            "OPS_ACTUATOR_TARGET_REF": self.namespace,
            "OPS_ACTUATOR_ALLOWED_NAMESPACES": json.dumps([self.namespace]),
            "OPS_ACTUATOR_ALLOWED_WORKLOADS": json.dumps(
                {
                    self.namespace: {
                        "target": {"kind": "deployment", "min_replicas": 0, "max_replicas": 3}
                    }
                }
            ),
        }
        log = (self.directory / f"actuator-{port}.log").open("w")
        process = subprocess.Popen(
            [sys.executable, "-m", "opencitadel_ops_actuator.main"], env=env, stdout=log, stderr=log
        )
        self.processes.append((process, port, log))
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("real actuator process exited during startup")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return len(self.processes) - 1
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("real actuator did not start")

    async def call(self, tool, arguments, instance=0):
        port = self.processes[instance][1]
        async with (
            streamablehttp_client(
                f"http://127.0.0.1:{port}/mcp",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=timedelta(seconds=10),
            ) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            if result.isError:
                raise RuntimeError("real MCP tool returned protocol error")
            value = result.structuredContent
            if not value:
                value = json.loads(
                    next(item.text for item in result.content if item.type == "text")
                )
            self.evidence.append(
                {
                    "tool": tool,
                    "operation_id": arguments.get("operation_id"),
                    "state": value.get("state"),
                    "receipt": value.get("receipt"),
                    "error_code": value.get("error_code"),
                }
            )
            return value

    async def arguments(self, *, key=None, replicas=None, uid=None):
        capabilities = await self.call("get_capabilities", {})
        assert capabilities["effect_contract"]["resolve"] == "get_operation"
        assert capabilities["effect_contract"]["target_identity"] == "get_target"
        assert capabilities["effect_contract"]["max_journal_bytes"] == 512 * 1024
        target = await self.call(
            "get_target", {"namespace": self.namespace, "workload": "target", "kind": "deployment"}
        )
        args = {
            "namespace": self.namespace,
            "workload": "target",
            "kind": "deployment",
            "idempotency_key": key or "rem:" + str(uuid4()),
            "operation_id": str(uuid4()),
            "target_uid": uid or target["uid"],
        }
        if replicas is not None:
            args["replicas"] = replicas
        args["payload_digest"] = operation_payload_digest(
            "scale_workload" if replicas is not None else "restart_workload",
            self.namespace,
            self.namespace,
            "target",
            "deployment",
            args["target_uid"],
            {"replicas": replicas} if replicas is not None else {},
        )
        return args

    async def crash_after_patch(self, args):
        self.proxy.applied.clear()
        self.proxy.release.clear()
        self.proxy.hold_next_patch = True
        pending = asyncio.create_task(self.call("restart_workload", args))
        observed = await asyncio.to_thread(self.proxy.applied.wait, 10)
        if not observed:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            raise AssertionError("real Kubernetes PATCH was not observed")
        self.processes[0][0].kill()
        self.processes[0][0].wait(timeout=5)
        self.proxy.release.set()
        result = await asyncio.gather(pending, return_exceptions=True)
        assert isinstance(result[0], BaseException), "MCP result must have been lost"

    def close(self):
        for process, _, log in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            log.close()
        if self.proxy:
            self.proxy.close()
            configmaps = json.loads(
                self.kubectl("-n", self.namespace, "get", "configmaps", "-o", "json")
            )["items"]
            proof = {
                "case": self.case_name,
                "namespace": self.namespace,
                "patches": self.proxy.patches,
                "mcp": self.evidence,
                "journal": [
                    {
                        "name": item["metadata"]["name"],
                        "resource_version": item["metadata"]["resourceVersion"],
                        "record": json.loads(item["data"]["record.json"]),
                    }
                    for item in configmaps
                    if "record.json" in item.get("data", {})
                ],
            }
            evidence_root = Path(os.environ.get("ACTUATOR_EVIDENCE_DIR", str(self.directory)))
            evidence_root.mkdir(parents=True, exist_ok=True)
            (evidence_root / f"{self.namespace}.json").write_text(json.dumps(proof, indent=2))
        self.kubectl("delete", "namespace", self.namespace, "--wait=false")
        if hasattr(self, "kubeconfig"):
            self.kubeconfig.unlink(missing_ok=True)


@pytest.fixture
def live_actuator(tmp_path, request):
    if os.environ.get("ACTUATOR_K8S_INTEGRATION") != "1":
        pytest.skip("requires explicitly enabled disposable Kubernetes fixture")
    context = os.environ.get("PATROL_DEMO_CONTEXT", "")
    if not context.startswith("kind-opencitadel-patrol-") or not os.environ.get("KUBECONFIG"):
        pytest.fail("explicit disposable kind context and isolated KUBECONFIG are required")
    fixture = LiveActuatorFixture(tmp_path, request.node.name)
    try:
        yield fixture.start()
    finally:
        fixture.close()
