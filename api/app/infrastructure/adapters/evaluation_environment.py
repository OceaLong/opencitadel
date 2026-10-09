"""Docker-owned cells. Every mutation names and verifies its complete lease ownership.

No default pool, broker fallback, image pull, host mount or case capability is used.
Deployment composition must explicitly register this local-only adapter.
"""

import asyncio
import json
import re
from uuid import NAMESPACE_URL, uuid5

from app.domain.evaluation.configuration import digest


class DockerCommandError(RuntimeError):
    pass


async def docker(*arguments, stdin=None, timeout=60):  # noqa: ASYNC109 - subprocess deadline
    process = await asyncio.create_subprocess_exec(
        "docker",
        *map(str, arguments),
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(stdin), timeout)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        # Infrastructure command output may contain private runtime values; never publish it.
        raise DockerCommandError(error.decode(errors="replace")[:2000])
    return output.decode()


class DockerEnvironmentAdapter:
    revision = "docker-cell-v1"
    fixture_revisions = frozenset({"empty-home-v1"})
    healthcheck_revisions = frozenset({"owned-absence-v1"})

    def __init__(
        self, *, allowed_images, local_content_ids=False, command=docker, acceptance_owner=None
    ):
        self.allowed_images = frozenset(allowed_images)
        self.local_content_ids = local_content_ids
        self.command = command
        if acceptance_owner is not None and (
            len(acceptance_owner) != 2
            or any(
                not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", value) for value in acceptance_owner
            )
        ):
            raise ValueError("environment_acceptance_owner_invalid")
        self.acceptance_owner = acceptance_owner

    def validate(self, version, targets):
        if version.image_digest.reference not in self.allowed_images:
            raise ValueError("environment_image_unavailable")
        if version.image_digest.kind == "local_content_id" and not self.local_content_ids:
            raise ValueError("environment_local_image_forbidden")
        if targets:
            raise ValueError("environment_network_capability_unavailable")

    def labels(self, lease, role):
        return {
            **(
                {
                    "opencitadel.e04.acceptance.project": self.acceptance_owner[0],
                    "opencitadel.e04.acceptance.run": self.acceptance_owner[1],
                }
                if self.acceptance_owner
                else {}
            ),
            "opencitadel.e04.lease": str(lease.id),
            "opencitadel.e04.generation": str(lease.generation),
            "opencitadel.e04.version": str(lease.environment_version),
            "opencitadel.e04.scope": digest(lease.case_slot.workspace),
            "opencitadel.e04.namespace": lease.namespace,
            "opencitadel.e04.role": role,
            "opencitadel.e04.operation": str(
                uuid5(NAMESPACE_URL, f"{lease.id}:{lease.generation}:{role}")
            ),
        }

    async def inspect(self, kind, identity):
        # Absence is confirmed by exact ID inventory; other Docker failures propagate.
        ids = (
            await self.command(kind, "ls", "-aq", "--no-trunc")
            if kind == "container"
            else await self.command(kind, "ls", "-q", "--no-trunc")
        ).split()
        if identity not in ids:
            return None
        return json.loads(await self.command(kind, "inspect", identity))[0]

    async def resources(self, lease):
        found = []
        for kind in ("container", "network"):
            args = [kind, "ls", "-q", "--no-trunc"]
            if kind == "container":
                args.append("-a")
            args += ["--filter", "label=opencitadel.e04.namespace=" + lease.namespace]
            for identity in (await self.command(*args)).split():
                info = await self.inspect(kind, identity)
                labels = (
                    (
                        info.get("Config", {}).get("Labels", {})
                        if kind == "container"
                        else info.get("Labels", {})
                    )
                    if info
                    else {}
                )
                role = labels.get("opencitadel.e04.role")
                if role is None or {
                    key: value
                    for key, value in labels.items()
                    if key.startswith("opencitadel.e04.")
                } != self.labels(lease, role):
                    raise ValueError("environment_resource_ownership_mismatch")
                found.append({"kind": kind, "id": identity, "role": role, "labels": labels})
        return found

    async def create_container(
        self, lease, role, image, command, *, network="none", extra=(), version
    ):
        found = [resource for resource in await self.resources(lease) if resource["role"] == role]
        if found:
            if len(found) != 1:
                raise ValueError("environment_resource_ambiguous")
            return found[0]["id"]
        args = [
            "create",
            "--pull",
            "never",
            "--name",
            lease.namespace + "-" + role,
            "--network",
            network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(version.limits.pids),
            "--memory",
            str(version.limits.memory_mb) + "m",
            "--cpus",
            str(version.limits.cpu_millis / 1000),
            "--user",
            "1000:1000",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=134217728,mode=1777",
            "--tmpfs",
            "/home/ubuntu:rw,nosuid,nodev,size=268435456,uid=1000,gid=1000",
            "--tmpfs",
            "/run:rw,nosuid,nodev,size=16777216,uid=1000,gid=1000",
        ]
        for key, value in self.labels(lease, role).items():
            args += ["--label", key + "=" + value]
        args += [*extra, "--entrypoint", command[0], image, *command[1:]]
        identity = (await self.command(*args)).strip()
        await self.command("start", identity)
        return identity

    async def prepare(self, lease, operation, version, targets):
        self.validate(version, targets)
        # Explicit inspect fails on absent cache; never resolves a tag or pulls.
        image = json.loads(await self.command("image", "inspect", version.image_digest.reference))[
            0
        ]
        await self.create_container(
            lease, "case", image["Id"], ["/bin/sh", "-c", "exec sleep infinity"], version=version
        )
        return {
            "resources": await self.resources(lease),
            "actual_versions": {
                "environment_version": str(version.id),
                "environment_revision": version.revision,
                "image_identity": version.image_digest.model_dump(mode="json"),
                "actual_image_id": image["Id"],
                "fixture_revision": version.fixture_revision,
                "adapter_revision": self.revision,
                "healthcheck_revision": version.healthcheck_revision,
                "namespace": lease.namespace,
                "generation": lease.generation,
                "network": "none",
                "limits": version.limits.model_dump(mode="json"),
            },
        }

    async def case(self, lease):
        resources = [item for item in await self.resources(lease) if item["role"] == "case"]
        if len(resources) != 1:
            raise ValueError("environment_case_unavailable")
        return resources[0]["id"]

    async def reset(self, lease, operation, version, targets):
        identity = await self.case(lease)
        # Fixture is immutable code, not an administrator-imported script.
        await self.command(
            "exec",
            identity,
            "/bin/sh",
            "-c",
            "printf 'e04-empty-home-v1\\n' > /home/ubuntu/.e04-fixture",
        )
        return {"resources": await self.resources(lease)}

    async def verify(self, lease, operation, version, targets):
        if operation.phase == "verify_clean":
            return {"verified": not await self.resources(lease), "resources": []}
        identity = await self.case(lease)
        info = await self.inspect("container", identity)
        host = info["HostConfig"]
        verified = bool(
            info["State"]["Running"]
            and host["ReadonlyRootfs"]
            and host["NetworkMode"] == "none"
            and host["CapDrop"] == ["ALL"]
            and not host.get("Privileged")
            and not host.get("Binds")
            and not host.get("CapAdd")
        )
        baseline = await self.command("exec", identity, "cat", "/home/ubuntu/.e04-fixture")
        return {
            "verified": verified and baseline == "e04-empty-home-v1\n",
            "resources": await self.resources(lease),
        }

    async def cleanup(self, lease, operation, version, targets):
        resources = await self.resources(lease)
        for resource in sorted(resources, key=lambda item: item["kind"] != "container"):
            info = await self.inspect(resource["kind"], resource["id"])
            if info is None:
                continue
            labels = (
                info["Config"].get("Labels", {})
                if resource["kind"] == "container"
                else info.get("Labels", {})
            )
            if labels != resource["labels"]:
                raise ValueError("environment_resource_ownership_mismatch")
            await self.command(
                resource["kind"],
                "rm",
                *(["-f"] if resource["kind"] == "container" else []),
                resource["id"],
            )
        return {"resources": []}


class DockerNetworkEnvironmentAdapter(DockerEnvironmentAdapter):
    """Local owned HTTP fixture cell; production/external inventories need their own adapter.

    One target bridge and one case bridge; only the fixed HTTP proxy is dual-homed.
    All six components are fresh lease-owned resources. A fixed NET_ADMIN bootstrap
    installs netns firewall rules then exits before the case may execute any tools.
    """

    revision = "docker-http-cell-v1"
    tool_names = frozenset({"shell_execute", "read_file", "write_file", "browser_navigate"})

    def __init__(
        self,
        *,
        allowed_images,
        fixture_image,
        bootstrap_image,
        local_content_ids=False,
        command=docker,
        acceptance_owner=None,
    ):
        super().__init__(
            allowed_images=allowed_images,
            local_content_ids=local_content_ids,
            command=command,
            acceptance_owner=acceptance_owner,
        )
        import re

        if not all(
            re.fullmatch(r"(?:[a-zA-Z0-9./:_-]+@)?sha256:[0-9a-f]{64}", value)
            for value in (fixture_image, bootstrap_image)
        ):
            raise ValueError("environment_helper_image_unpinned")
        self.fixture_image, self.bootstrap_image = fixture_image, bootstrap_image

    def validate(self, version, targets):
        super().validate(version, ())
        if (
            len(targets) != 1
            or targets[0].kind not in {"http", "mcp", "a2a"}
            or targets[0].endpoint != "http://allowed.e04.test:8081"
            or targets[0].shared
        ):
            raise ValueError("environment_owned_http_target_required")

    async def network(self, lease, role):
        found = [resource for resource in await self.resources(lease) if resource["role"] == role]
        if found:
            return found[0]["id"]
        args = [
            "network",
            "create",
            "--internal",
            "--driver",
            "bridge",
            "--opt",
            "com.docker.network.bridge.gateway_mode_ipv4=isolated",
            "--ipv6=false",
        ]
        for key, value in self.labels(lease, role).items():
            args += ["--label", key + "=" + value]
        return (await self.command(*args, lease.namespace + "-" + role)).strip()

    async def ip(self, identity, network):
        info = await self.inspect("container", identity)
        matches = [
            n["IPAddress"]
            for n in info["NetworkSettings"]["Networks"].values()
            if n["NetworkID"] == network
        ]
        if len(matches) != 1:
            raise ValueError("environment_network_membership_invalid")
        return matches[0]

    async def bootstrap(self, lease, case, proxy_ip, version):
        from app.infrastructure.adapters.evaluation_cell_programs import FIREWALL

        helper = await self.create_container(
            lease,
            "bootstrap",
            self.bootstrap_image,
            ["/bin/sh", "-c", FIREWALL, "e04-firewall", proxy_ip],
            network="container:" + case,
            extra=("--user", "0:0", "--cap-add", "NET_ADMIN"),
            version=version,
        )
        code = await self.command("wait", helper)
        if code.strip() != "0":
            raise ValueError("environment_firewall_bootstrap_failed")
        rules = await self.command("logs", helper)
        if ":OUTPUT DROP" not in rules or f"-d {proxy_ip}/32" not in rules:
            raise ValueError("environment_firewall_attestation_failed")
        info = await self.inspect("container", helper)
        if [cap.removeprefix("CAP_") for cap in info["HostConfig"].get("CapAdd", [])] != [
            "NET_ADMIN"
        ] or info["State"]["Running"]:
            raise ValueError("environment_bootstrap_capability_invalid")
        await self.command("container", "rm", helper)
        return {
            "image_id": self.bootstrap_image,
            "rules_digest": digest(rules),
            "rules": rules,
            "helper_id": helper,
            "helper_exited": True,
            "proxy_ip": proxy_ip,
        }

    async def prepare(self, lease, operation, version, targets):
        from app.infrastructure.adapters.evaluation_cell_programs import HTTP_FIXTURE, HTTP_PROXY

        self.validate(version, targets)
        for image in (version.image_digest.reference, self.fixture_image, self.bootstrap_image):
            await self.command("image", "inspect", image)
        cell = await self.network(lease, "cell-network")
        back = await self.network(lease, "target-network")
        case = await self.create_container(
            lease,
            "case",
            version.image_digest.reference,
            ["/bin/sh", "-c", "exec sleep infinity"],
            network=cell,
            version=version,
        )
        allowed = await self.create_container(
            lease,
            "allowed",
            self.fixture_image,
            ["python", "-u", "-c", HTTP_FIXTURE],
            network=back,
            version=version,
        )
        await self.create_container(
            lease,
            "denied",
            self.fixture_image,
            ["python", "-u", "-c", HTTP_FIXTURE],
            network=back,
            version=version,
        )
        proxy = await self.create_container(
            lease,
            "proxy",
            self.fixture_image,
            ["/bin/sh", "-c", "exec sleep infinity"],
            network=cell,
            version=version,
        )
        info = await self.inspect("container", proxy)
        if back not in [item["NetworkID"] for item in info["NetworkSettings"]["Networks"].values()]:
            await self.command("network", "connect", back, proxy)
        proxy_ip = await self.ip(proxy, cell)
        allowed_ip = await self.ip(allowed, back)
        attestation = await self.bootstrap(lease, case, proxy_ip, version)
        # The proxy program exposes only one listener on the case-side interface.
        await self.command(
            "exec",
            "-d",
            proxy,
            "python",
            "-u",
            "-c",
            HTTP_PROXY,
            "allowed.e04.test:8081",
            allowed_ip,
            "8081",
            proxy_ip,
        )
        await self.command(
            "exec",
            case,
            "/bin/sh",
            "-c",
            "printf '%s' \"$1\" > /home/ubuntu/.e04-proxy",
            "e04",
            f"http://{proxy_ip}:3128",
        )
        await self.command(
            "exec",
            "-d",
            "-e",
            "SANDBOX_ACCESS_TOKEN=" + self.access_token(lease),
            "-e",
            "HTTP_PROXY=" + f"http://{proxy_ip}:3128",
            "-e",
            "HTTPS_PROXY=" + f"http://{proxy_ip}:3128",
            "-e",
            "http_proxy=" + f"http://{proxy_ip}:3128",
            case,
            "/venv/bin/uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
        )
        resources = await self.resources(lease)
        return {
            "resources": resources,
            "actual_versions": {
                "environment_version": str(version.id),
                "environment_revision": version.revision,
                "image_identity": version.image_digest.model_dump(mode="json"),
                "actual_image_id": (await self.inspect("container", case))["Image"],
                "case_started_at": (await self.inspect("container", case))["State"]["StartedAt"],
                "fixture_revision": version.fixture_revision,
                "adapter_revision": self.revision,
                "healthcheck_revision": version.healthcheck_revision,
                "namespace": lease.namespace,
                "generation": lease.generation,
                "limits": version.limits.model_dump(mode="json"),
                "network": {
                    "cell": cell,
                    "targets": back,
                    "proxy_ip": proxy_ip,
                    "allowed_ip": allowed_ip,
                    "firewall": attestation,
                },
                "target_revisions": [
                    ref.model_dump(mode="json") for ref in version.allowed_targets
                ],
                "credential_revisions": [],
            },
        }

    async def verify(self, lease, operation, version, targets):
        if operation.phase == "verify_clean":
            return await super().verify(lease, operation, version, targets)
        case = await self.case(lease)
        resources = await self.resources(lease)
        if any(resource["role"] == "bootstrap" for resource in resources):
            return {"verified": False}
        info = await self.inspect("container", case)
        host = info["HostConfig"]
        cell = next(resource["id"] for resource in resources if resource["role"] == "cell-network")
        proxy = next(resource["id"] for resource in resources if resource["role"] == "proxy")
        proxy_ip = await self.ip(proxy, cell)
        # Reinstallation is a fixed safe gate and proves rules in the current namespace;
        # it runs before ready and never grants the untrusted case a capability.
        proof = await self.bootstrap(lease, case, proxy_ip, version)
        verified = bool(
            info["State"]["Running"]
            and host["ReadonlyRootfs"]
            and host["CapDrop"] == ["ALL"]
            and not host.get("CapAdd")
            and not host.get("Privileged")
            and not host.get("Binds")
            and len(info["NetworkSettings"]["Networks"]) == 1
        )
        for resource in resources:
            if resource["kind"] == "network":
                network = await self.inspect("network", resource["id"])
                verified = (
                    verified
                    and network["Internal"]
                    and not network["EnableIPv6"]
                    and network["Options"].get("com.docker.network.bridge.gateway_mode_ipv4")
                    == "isolated"
                )
        return {
            "verified": verified,
            "resources": await self.resources(lease),
            "actual_versions": dict(lease.actual_versions) | {"firewall_verified": proof},
        }

    def access_token(self, lease):
        return digest({"owner": self.labels(lease, "case"), "purpose": "local-control-v1"})

    async def tool_pack(self, lease, name, targets, sandbox_factory):
        from app.domain.services.tools.browser import BrowserTool
        from app.domain.services.tools.file import FileTool
        from app.domain.services.tools.shell import ShellTool
        from app.infrastructure.adapters.evaluation_sandbox import LeaseBrowser

        if name == "browser_navigate":
            return BrowserTool(LeaseBrowser(self, lease, [target.endpoint for target in targets]))
        sandbox = await sandbox_factory.attach_environment(
            adapter=self, lease=lease, access_token=self.access_token(lease)
        )
        return ShellTool(sandbox) if name == "shell_execute" else FileTool(sandbox)

    async def check_runtime(self, lease):
        case = await self.case(lease)
        info = await self.inspect("container", case)
        actual = lease.actual_versions
        if (
            not info["State"]["Running"]
            or info["State"]["StartedAt"] != actual.get("case_started_at")
            or info["Image"] != actual.get("actual_image_id")
        ):
            raise ValueError("environment_runtime_restarted_or_changed")
        resources = await self.resources(lease)
        if {item["id"] for item in resources} != {item["id"] for item in lease.resources}:
            raise ValueError("environment_runtime_resources_changed")
        host = info["HostConfig"]
        if (
            not host["ReadonlyRootfs"]
            or host.get("Privileged")
            or host.get("Binds")
            or host.get("CapAdd")
            or host.get("CapDrop") != ["ALL"]
        ):
            raise ValueError("environment_runtime_policy_changed")
        cell = actual["network"]["cell"]
        if len(info["NetworkSettings"]["Networks"]) != 1 or cell not in {
            item["NetworkID"] for item in info["NetworkSettings"]["Networks"].values()
        }:
            raise ValueError("environment_runtime_network_changed")
