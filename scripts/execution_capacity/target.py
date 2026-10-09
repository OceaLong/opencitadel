"""Exact isolated deployment checks for the capacity host driver.

No command executes on import. Every attached container is inventoried. Unknown
services are conservatively producers; callers cannot exempt an application by
labelling it storage. This module does not establish database drain evidence.
"""

import re
from uuid import UUID


def verify_deployment(binding, network, containers, *, live_endpoints=False):
    """Return original producer running states or reject the entire deployment."""
    try:
        UUID(binding["invocation"])
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,62}", binding["project"]):
            raise ValueError("invalid deployment project")
        labels = {
            "com.docker.compose.project": binding["project"],
            "com.opencitadel.acceptance.project": binding["project"],
            "com.opencitadel.acceptance.run": binding["invocation"],
        }
        attached = (
            {identity for identity, row in containers.items() if row["State"]["Running"]}
            if live_endpoints
            else set(binding["containers"])
        )
        if (
            network["Id"] != binding["network_id"]
            or network["Internal"] is not True
            or not re.fullmatch(r"[0-9a-f]{64}", network["Id"])
            or any(network["Labels"].get(key) != value for key, value in labels.items())
            or set(network["Containers"]) != attached
            or set(containers) != set(binding["containers"])
        ):
            raise ValueError("shared or changed deployment network")
        producers = {}
        storage_services = {"postgres", "redis", "minio", "inference-provider"}
        for identity, expected in binding["containers"].items():
            actual = containers[identity]
            expected_role = "storage" if expected["service"] in storage_services else "producer"
            if expected["service"] == "opencitadel-sandbox-broker":
                broker = binding["broker"]
                mounts = {
                    m["Destination"]: [m["Type"], m["Source"], m["RW"]] for m in actual["Mounts"]
                }
                if (
                    broker["container_id"] != identity
                    or not actual["State"]["Running"]
                    or actual["Config"].get("Entrypoint") not in (None, [])
                    or next(
                        (
                            v.split("=", 1)[1]
                            for v in actual["Config"].get("Env", [])
                            if v.startswith("EVALUATION_BROKER_JOURNAL_PATH=")
                        ),
                        "/var/lib/opencitadel-evaluation/operations.sqlite",
                    )
                    != "/var/lib/opencitadel-evaluation/operations.sqlite"
                    or actual["Config"].get("Cmd")
                    != ["python", "-m", "app.infrastructure.external.sandbox.broker"]
                    or mounts != broker["mounts"]
                    or mounts.get("/var/run/docker.sock") != ["bind", "/var/run/docker.sock", True]
                    or mounts.get("/var/lib/opencitadel-evaluation", [None])[0] != "volume"
                    or any(
                        path
                        not in {
                            "/var/run/docker.sock",
                            "/var/lib/opencitadel-evaluation",
                            "/etc/opencitadel-evaluation/environment.json",
                            "/etc/opencitadel-evaluation/budget.json",
                        }
                        for path in mounts
                    )
                    or any(
                        value[0] != "bind" or value[2]
                        for path, value in mounts.items()
                        if path.startswith("/etc/")
                    )
                ):
                    raise ValueError("broker command or exact socket/receipt/config mounts differ")
                expected_role = "capability-service"
            networks = actual["NetworkSettings"]["Networks"]
            if (
                not re.fullmatch(r"[0-9a-f]{64}", identity)
                or actual["Id"] != identity
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected["image"])
                or actual["Image"] != expected["image"]
                or expected["role"] != expected_role
                or any(
                    actual["Config"]["Labels"].get(key) != value for key, value in labels.items()
                )
                or actual["Config"]["Labels"].get("com.docker.compose.service")
                != expected["service"]
                or actual["HostConfig"]["Privileged"] is not False
                or actual["HostConfig"]["NetworkMode"] in {"host", "none"}
                or len(networks) != 1
                or {value["NetworkID"] for value in networks.values()} != {network["Id"]}
                or any(actual["NetworkSettings"]["Ports"].values())
                or type(actual["State"]["Running"]) is not bool
            ):
                raise ValueError("foreign, exposed or changed deployment container")
            if expected_role == "producer":
                producers[identity] = actual["State"]["Running"]
        if not producers or not any(
            item["service"] == "postgres" for item in binding["containers"].values()
        ):
            raise ValueError("deployment requires producers and dedicated postgres")
        return producers
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("incomplete deployment ownership") from exc
