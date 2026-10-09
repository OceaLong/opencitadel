"""Real owned-container evidence is explicit opt-in; unit tests never imply enforcement."""

import os
from uuid import uuid4

import pytest

from app.domain.evaluation.environment import (
    CaseSlot,
    EnvironmentLease,
    EnvironmentOperation,
    EnvironmentVersion,
    ImageIdentity,
)
from app.infrastructure.adapters.evaluation_environment import DockerEnvironmentAdapter
from tests.app.evaluation_image_support import evaluation_test_image

PYTHON_ID = evaluation_test_image(
    "FIXTURE", "sha256:4185d2c55ba89731509d80ef7972b16c75553fdda1cb61d46ede5aac3595b1aa"
)


def inputs(image=PYTHON_ID):
    version = EnvironmentVersion(
        id=uuid4(),
        image_digest=ImageIdentity(kind="local_content_id", value=image),
        fixture_revision="empty-home-v1",
        reset_adapter="docker",
        adapter_revision="docker-cell-v1",
        healthcheck_revision="owned-absence-v1",
    )
    lease = EnvironmentLease(
        id=uuid4(),
        environment_version=version.id,
        case_slot=CaseSlot(
            workspace="user:e04-real-owned-test",
            batch_id=uuid4(),
            case_id=uuid4(),
            config_version=uuid4(),
            repeat=1,
        ),
        generation=1,
        revision=2,
        state="preparing",
    )
    operation = EnvironmentOperation(
        id=uuid4(),
        lease_id=lease.id,
        generation=1,
        lease_revision=2,
        phase="prepare",
        claim_generation=1,
    )
    return version, lease, operation


def test_network_target_without_enforcement_is_rejected():
    version, _, _ = inputs()
    adapter = DockerEnvironmentAdapter(allowed_images=(PYTHON_ID,), local_content_ids=True)
    with pytest.raises(ValueError, match="network_capability_unavailable"):
        adapter.validate(version, (object(),))


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("E04_REAL_DOCKER") != "1", reason="explicit owned Docker test opt-in required"
)
async def test_real_owned_fixture_cleanup_rebuild():
    version, lease, operation = inputs()
    adapter = DockerEnvironmentAdapter(allowed_images=(PYTHON_ID,), local_content_ids=True)
    exact = []
    try:
        receipt = await adapter.prepare(lease, operation, version, ())
        exact += receipt["resources"]
        assert receipt["actual_versions"]["actual_image_id"] == PYTHON_ID
        assert await adapter.prepare(lease, operation, version, ()) == receipt
        await adapter.reset(lease, operation, version, ())
        case = await adapter.case(lease)
        await adapter.command(
            "exec", case, "sh", "-c", "echo unique-contamination > /home/ubuntu/marker"
        )
        await adapter.cleanup(lease, operation, version, ())
        await adapter.cleanup(lease, operation, version, ())
        assert (
            await adapter.verify(
                lease, operation.model_copy(update={"phase": "verify_clean"}), version, ()
            )
        )["verified"]
        new = lease.model_copy(update={"generation": 2})
        try:
            rebuilt = await adapter.prepare(new, operation, version, ())
            exact += rebuilt["resources"]
            await adapter.reset(new, operation, version, ())
            await adapter.command(
                "exec", await adapter.case(new), "sh", "-c", "test ! -e /home/ubuntu/marker"
            )
            assert (
                await adapter.verify(
                    new, operation.model_copy(update={"phase": "verify_ready"}), version, ()
                )
            )["verified"]
        finally:
            await adapter.cleanup(new, operation, version, ())
    finally:
        await adapter.cleanup(lease, operation, version, ())
        print("E04_OWNED_RESOURCES=" + __import__("json").dumps(exact, sort_keys=True))


SANDBOX_ID = evaluation_test_image(
    "SANDBOX", "sha256:2985396f077503042754ff2673e0d10a3c8903c03b9a38ec711a9e64947659fd"
)
BOOTSTRAP_ID = evaluation_test_image(
    "BOOTSTRAP", "sha256:b200139c59c438b5ab199e7b0886c95583cfb1a2508de05dd77a25930d99d13f"
)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("E04_REAL_DOCKER") != "1", reason="explicit owned Docker test opt-in required"
)
async def test_real_shell_chromium_network_denials():
    import json

    from app.domain.evaluation.environment import TestTarget, VersionRef
    from app.infrastructure.adapters.evaluation_environment import DockerNetworkEnvironmentAdapter

    target = TestTarget(
        id=uuid4(),
        physical_resource="e04-owned-fixture",
        kind="http",
        endpoint="http://allowed.e04.test:8081",
    )
    version, lease, operation = inputs(SANDBOX_ID)
    version = version.model_copy(
        update={
            "adapter_revision": "docker-http-cell-v1",
            "allowed_targets": (VersionRef(id=target.id, revision=1),),
        }
    )
    adapter = DockerNetworkEnvironmentAdapter(
        allowed_images=(SANDBOX_ID,),
        fixture_image=PYTHON_ID,
        bootstrap_image=BOOTSTRAP_ID,
        local_content_ids=True,
    )
    exact = []
    try:
        receipt = await adapter.prepare(lease, operation, version, (target,))
        exact = receipt["resources"]
        lease = lease.model_copy(
            update={"resources": tuple(exact), "actual_versions": receipt["actual_versions"]}
        )
        await adapter.reset(lease, operation, version, (target,))
        assert (
            await adapter.verify(
                lease, operation.model_copy(update={"phase": "verify_ready"}), version, (target,)
            )
        )["verified"]
        case = await adapter.case(lease)
        from app.domain.services.tools.shell import ShellTool
        from app.infrastructure.external.sandbox.factory import SandboxFactory

        class LocalFactory:
            _host = None
            _quota = None
            attach_environment = SandboxFactory.attach_environment

            async def current_settings(self, **kwargs):
                return None

        sandbox = await LocalFactory().attach_environment(
            adapter=adapter, lease=lease, access_token=adapter.access_token(lease)
        )
        try:
            shell = ShellTool(sandbox)
            result = await shell.invoke(
                "shell_execute",
                session_id="e04-owned-shell",
                exec_dir="/home/ubuntu",
                command="curl --fail --max-time 5 http://allowed.e04.test:8081/actual-shell-tool",
            )
            assert result.success, result
            await sandbox.wait_process("e04-owned-shell", seconds=5)
            output = await sandbox.read_shell_output("e04-owned-shell")
            assert "owned-allowed-fixture" in str(output.data), output
        finally:
            await sandbox.client.aclose()
        denied = next(r["id"] for r in exact if r["role"] == "denied")
        allowed = next(r["id"] for r in exact if r["role"] == "allowed")
        proxy = next(r["id"] for r in exact if r["role"] == "proxy")
        network = receipt["actual_versions"]["network"]
        denied_ip = await adapter.ip(denied, network["targets"])
        proxy_url = "http://" + network["proxy_ip"] + ":3128"
        # Positive denied endpoint control from an owned trusted same-network fixture.
        await adapter.command(
            "exec",
            allowed,
            "python",
            "-c",
            "import urllib.request; assert urllib.request.urlopen('http://"
            + denied_ip
            + ":8081/control-positive',timeout=3).status==200",
        )
        script = """import urllib.request,urllib.error,socket,json
proxy,denied,allowed_ip,proxy_ip=__import__('sys').argv[1:]
opener=urllib.request.build_opener(urllib.request.ProxyHandler({'http':proxy}))
assert opener.open('http://allowed.e04.test:8081/shell-positive',timeout=5).status==200
for url in ['http://denied.e04.test:8081/proxy-denied','http://'+denied+':8081/ip-literal','http://allowed.e04.test:8082/wrong-port','http://alias.e04.test:8081/alias','http://allowed.e04.test:8081/redirect']:
 try: opener.open(url,timeout=3); raise AssertionError(url)
 except (urllib.error.URLError,TimeoutError): pass
for ip,port in [(denied,8081),(allowed_ip,8081),(proxy_ip,8082),('127.0.0.11',53)]:
 try: socket.create_connection((ip,port),timeout=.7); raise AssertionError((ip,port))
 except (OSError,TimeoutError): pass
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(.7)
try: s.sendto(bytes.fromhex('123401000001000000000000')+b'\\x08unowned\\x04test\\x00\\x00\\x01\\x00\\x01',('127.0.0.11',53))
except PermissionError:pass
try: s.recv(4096);raise AssertionError('DNS escaped')
except TimeoutError:pass
print('SHELL_ALLOWED_AND_BYPASS_DENIED')
"""
        assert "SHELL_ALLOWED_AND_BYPASS_DENIED" in await adapter.command(
            "exec",
            case,
            "/venv/bin/python3",
            "-c",
            script,
            proxy_url,
            denied_ip,
            network["allowed_ip"],
            network["proxy_ip"],
        )
        # Real Chromium runs in the actual confined case, including redirect/subresource/fetch.
        browser = """import subprocess,sys
common=['/usr/bin/chromium','--headless','--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-background-networking','--dump-dom','--timeout=2500','--virtual-time-budget=800']
result=subprocess.run(common+['--proxy-server='+sys.argv[1],'http://allowed.e04.test:8081/browser-positive'],capture_output=True,text=True,timeout=15)
assert 'owned-allowed-fixture' in result.stdout, result.stderr
redirect=subprocess.run(common+['--proxy-server='+sys.argv[1],'http://allowed.e04.test:8081/redirect'],capture_output=True,text=True,timeout=15)
assert 'owned-allowed-fixture' not in redirect.stdout
try:
 direct=subprocess.run(common+['http://'+sys.argv[2]+':8081/browser-direct'],capture_output=True,text=True,timeout=7)
 assert 'owned-allowed-fixture' not in direct.stdout
except subprocess.TimeoutExpired:pass
print('CHROMIUM_ALLOWED_AND_BYPASS_DENIED')
"""
        assert "CHROMIUM_ALLOWED_AND_BYPASS_DENIED" in await adapter.command(
            "exec", case, "/venv/bin/python3", "-c", browser, proxy_url, denied_ip, timeout=45
        )
        from app.domain.evaluation.recording import RecordedContract
        from app.domain.services.tools.capability_policy import READ_SAFE
        from app.infrastructure.adapters.evaluation_external import RegisteredHTTPTestExecutor

        contract = RecordedContract(
            name="mcp_owned_echo",
            pack="mcp",
            schema_body={
                "type": "function",
                "function": {"name": "mcp_owned_echo", "parameters": {"type": "object"}},
            },
            policy=READ_SAFE,
            connector_id="owned-mcp",
            source_name="echo",
            binding_revision="fixed-v1",
            authority_revision="fixed-v1",
        )
        mcp = target.model_copy(
            update={
                "kind": "mcp",
                "protocol": "mcp-stateless-json-2025-03-26",
                "connector_id": "owned-mcp",
                "connector_revision": "fixed-v1",
                "contracts": (contract,),
            }
        )
        result = (
            await RegisteredHTTPTestExecutor(mcp)
            .bind(adapter, lease)
            .invoke(mcp, (), None, None, "mcp_owned_echo", {})
        )
        assert result["success"]
        assert result["data"]["content"][0]["text"] == "owned-test-tool-result"
        contract = contract.model_copy(
            update={
                "name": "call_remote_agent",
                "pack": "a2a",
                "connector_id": "owned-a2a",
                "schema_body": {
                    "type": "function",
                    "function": {
                        "name": "call_remote_agent",
                        "parameters": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}, "query": {"type": "string"}},
                            "required": ["id", "query"],
                        },
                    },
                },
            }
        )
        a2a = mcp.model_copy(
            update={
                "kind": "a2a",
                "protocol": "a2a-jsonrpc-0.3",
                "connector_id": "owned-a2a",
                "contracts": (contract,),
                "allowed_agent_ids": ("fixture-agent",),
            }
        )
        executor = RegisteredHTTPTestExecutor(a2a).bind(adapter, lease)
        assert (
            await executor.invoke(
                a2a, (), None, None, "call_remote_agent", {"id": "fixture-agent", "query": "test"}
            )
        )["success"]
        with pytest.raises(ValueError, match="a2a_agent_denied"):
            await executor.invoke(
                a2a,
                (),
                None,
                None,
                "call_remote_agent",
                {"id": "production-agent", "query": "test"},
            )
        denied_log = await adapter.command("exec", denied, "cat", "/tmp/requests")
        assert denied_log.strip() == "/control-positive"
        await adapter.command("stop", proxy)
        await adapter.command(
            "exec",
            case,
            "/venv/bin/python3",
            "-c",
            "import urllib.request,sys\np=urllib.request.build_opener(urllib.request.ProxyHandler({'http':sys.argv[1]}))\ntry:p.open('http://allowed.e04.test:8081/proxy-death',timeout=2);raise AssertionError('proxy death bypass')\nexcept OSError:pass",
            proxy_url,
        )
    finally:
        exact = await adapter.resources(lease) or exact
        await adapter.cleanup(lease, operation, version, (target,))
        assert not await adapter.resources(lease)
        print("E04_NETWORK_OWNED_RESOURCES=" + json.dumps(exact, sort_keys=True))
