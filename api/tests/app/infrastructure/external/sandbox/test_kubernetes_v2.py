from app.infrastructure.external.sandbox.kubernetes import build_sandbox_pod
from core.config import DeploymentSettings


def test_kubernetes_sandbox_pod_has_the_same_closed_resource_envelope() -> None:
    settings = DeploymentSettings(
        sandbox_image="opencitadel-sandbox:v2",
        sandbox_name_prefix="opencitadel-sandbox",
        sandbox_k8s_namespace="opencitadel",
        sandbox_k8s_pod_label="app=opencitadel-sandbox",
        sandbox_http_proxy="http://egress-proxy:3128",
        sandbox_https_proxy="http://egress-proxy:3128",
    )

    pod = build_sandbox_pod(
        settings,
        sandbox_id="opencitadel-sandbox-deadbeef",
        access_token="run-token",
        ttl_minutes=60,
    )

    assert pod["metadata"]["labels"] == {
        "app": "opencitadel-sandbox",
        "opencitadel.io/sandbox": "true",
    }
    spec = pod["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert spec["activeDeadlineSeconds"] == 3600
    container = spec["containers"][0]
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
        "runAsUser": 1000,
    }
    assert container["resources"]["limits"] == {
        "cpu": "2",
        "memory": "2Gi",
    }
    assert {item["name"]: item["value"] for item in container["env"]}[
        "SANDBOX_ACCESS_TOKEN"
    ] == "run-token"
