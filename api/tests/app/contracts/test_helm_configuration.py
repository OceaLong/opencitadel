"""Helm configuration, credential persistence, and namespace contracts."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
CHART = ROOT / "deploy/helm/opencitadel"
HELM = shutil.which("helm")
pytestmark = pytest.mark.skipif(HELM is None, reason="helm is not installed")


def _render(tmp_path: Path, overrides=None, *, chart=CHART, upgrade=False, succeeds=True):
    values = {"publicUrl": "https://citadel.example.test", **(overrides or {})}
    path = tmp_path / "values.json"
    path.write_text(json.dumps(values))
    command = [
        HELM,
        "template",
        "citadel",
        str(chart),
        "--namespace",
        "team-citadel",
        "--values",
        str(path),
    ]
    if upgrade:
        command.append("--is-upgrade")
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if not succeeds:
        assert completed.returncode != 0
        return completed.stderr
    assert completed.returncode == 0, completed.stderr
    return [document for document in yaml.safe_load_all(completed.stdout) if document]


def _resource(documents, kind, name):
    return next(
        document
        for document in documents
        if document["kind"] == kind and document["metadata"]["name"] == name
    )


def test_effect_integrity_quarantine_alert_renders_with_monitoring_enabled(tmp_path):
    documents = _render(tmp_path, {"monitoring": {"prometheusRule": {"enabled": True}}})
    rule = next(
        rule
        for document in documents
        if document["kind"] == "PrometheusRule"
        for group in document["spec"]["groups"]
        for rule in group["rules"]
        if rule["alert"] == "OpencitadelEffectIntegrityConflict"
    )
    assert rule["expr"] == "max(execution_effect_integrity_conflict_rows) > 0"
    assert rule["labels"]["severity"] == "critical"
    assert rule["for"] == "0m"


def _environment(document):
    return {
        entry["name"]: entry.get("value", entry.get("valueFrom"))
        for entry in document["spec"]["template"]["spec"]["containers"][0]["env"]
    }


def _container_environment(documents, container):
    """Resolve Kubernetes Secret injection in the same precedence as the Pod."""
    environment = {}
    for source in container.get("envFrom", []):
        secret = _resource(documents, "Secret", source["secretRef"]["name"])
        environment.update(secret["stringData"])
    for entry in container.get("env", []):
        if "value" in entry:
            value = entry["value"]
        elif "secretKeyRef" in entry["valueFrom"]:
            reference = entry["valueFrom"]["secretKeyRef"]
            value = _resource(documents, "Secret", reference["name"])["stringData"][
                reference["key"]
            ]
        else:
            assert entry["valueFrom"] == {"fieldRef": {"fieldPath": "spec.nodeName"}}
            value = "contract-test-node"
        environment[entry["name"]] = str(value)
    return environment


def _lookup_chart(tmp_path, documents):
    """Stub only Kubernetes lookup responses, retaining the real Helm helpers."""
    chart = tmp_path / "chart"
    shutil.copytree(CHART, chart)
    helpers = chart / "templates/_helpers.tpl"
    source = helpers.read_text()
    lookup = 'lookup "v1" "Secret" $root.Release.Namespace $name'
    assert lookup in source
    helpers.write_text(source.replace(lookup, "(index $root.Values.fixtureSecrets $name)"))
    secrets = {
        document["metadata"]["name"]: {
            "data": {
                key: base64.b64encode(value.encode()).decode()
                for key, value in document["stringData"].items()
            }
        }
        for document in documents
        if document["kind"] == "Secret"
    }
    return chart, secrets


def test_default_stack_derives_topology_and_generates_separate_credentials(tmp_path):
    documents = _render(tmp_path)
    api = _environment(_resource(documents, "Deployment", "citadel-api"))
    kernel = _environment(_resource(documents, "Deployment", "citadel-execution-kernel"))
    assert api["POSTGRES_HOST"] == "citadel-postgres"
    assert api["POSTGRES_USER"] == "opencitadel_app"
    assert api["POSTGRES_DB"] == "opencitadel"
    assert api["REDIS_HOST"] == "citadel-redis"
    assert api["STORAGE_PROVIDER"] == "minio"
    assert api["MINIO_ENDPOINT"] == "citadel-minio:9000"
    assert api["SANDBOX_K8S_NAMESPACE"] == "team-citadel"
    assert api["SANDBOX_HTTP_PROXY"] == "http://citadel-egress-proxy:3128"
    assert api["FRONTEND_BASE_URL"] == "https://citadel.example.test"
    assert api["OAUTH_REDIRECT_BASE"] == "https://citadel.example.test/api/auth/oauth"
    assert api["COOKIE_SECURE"] == "true"
    assert api["OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS"] == "90"
    assert kernel["SANDBOX_K8S_NAMESPACE"] == api["SANDBOX_K8S_NAMESPACE"]
    assert kernel["POSTGRES_USER"] == "opencitadel_execution_kernel_runtime"
    assert _resource(documents, "StatefulSet", "citadel-minio")
    egress = _resource(documents, "NetworkPolicy", "citadel-execution-kernel")["spec"]["egress"]
    assert any(
        rule.get("ports") == [{"protocol": "TCP", "port": 9000}]
        and rule["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == "minio"
        for rule in egress
    )
    application = _resource(documents, "Secret", "citadel-secret")["stringData"]
    keys = [
        "API_KEY_SECRET",
        "AUDIT_SIGNING_KEY",
        "JWT_SECRET",
        "SESSION_SECRET",
        "SANDBOX_TOKEN_SEED",
        "DATABASE_AUTHORIZATION_SIGNING_SECRET",
        "PUBLIC_CURSOR_SECRET",
        "BOOTSTRAP_ADMIN_PASSWORD",
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
    ]
    values = [application[key] for key in keys]
    assert all(len(value) >= 32 for value in values)
    assert len(set(values)) == len(values)
    database_passwords = [
        application["POSTGRES_PASSWORD"],
        _resource(documents, "Secret", "citadel-postgres-bootstrap-secret")["stringData"][
            "POSTGRES_ADMIN_PASSWORD"
        ],
        _resource(documents, "Secret", "citadel-migration-secret")["stringData"][
            "POSTGRES_PASSWORD"
        ],
        _resource(documents, "Secret", "citadel-execution-kernel-secret")["stringData"][
            "POSTGRES_PASSWORD"
        ],
    ]
    assert len(set(database_passwords)) == 4


def test_rendered_production_environments_load_real_application_settings(tmp_path):
    documents = _render(tmp_path)
    api_pod = _resource(documents, "Deployment", "citadel-api")["spec"]["template"]["spec"]
    kernel_pod = _resource(documents, "Deployment", "citadel-execution-kernel")["spec"]["template"][
        "spec"
    ]
    script = """
import json
import os
from core.config import DeploymentSettings, sqlalchemy_sync_migration_database_uri
from sqlalchemy.engine import make_url

settings = DeploymentSettings(_env_file=None)
assert settings.postgres_password == os.environ["POSTGRES_PASSWORD"]
database = make_url(settings.sqlalchemy_database_uri)
migration_user = None
if settings.postgres_admin_user:
    migration_user = make_url(sqlalchemy_sync_migration_database_uri(settings)).username
print(json.dumps({
    "environment": settings.env,
    "storage": settings.storage_provider,
    "secure_cookie": settings.cookie_secure,
    "namespace": settings.sandbox_k8s_namespace,
    "postgres_host": settings.postgres_host,
    "postgres_database": settings.postgres_db,
    "postgres_user": settings.postgres_user,
    "database_uri_user": database.username,
    "database_uri_host": database.host,
    "migration_user": migration_user,
    "redis_host": settings.redis_host,
    "minio_endpoint": settings.minio_endpoint,
    "origin": settings.frontend_base_url,
    "oauth_redirect": settings.oauth_redirect_base,
    "api_previous_secrets": settings.api_key_previous_secrets,
}))
"""
    for process, container, database_user, migration_user in (
        ("api", api_pod["containers"][0], "opencitadel_app", None),
        (
            "migrate",
            api_pod["initContainers"][0],
            "opencitadel_app",
            "opencitadel_migration_runtime",
        ),
        (
            "kernel",
            kernel_pod["containers"][0],
            "opencitadel_execution_kernel_runtime",
            None,
        ),
    ):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(ROOT / "api"),
                **_container_environment(documents, container),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, f"{process}: {completed.stderr}"
        assert json.loads(completed.stdout) == {
            "environment": "production",
            "storage": "minio",
            "secure_cookie": True,
            "namespace": "team-citadel",
            "postgres_host": "citadel-postgres",
            "postgres_database": "opencitadel",
            "postgres_user": database_user,
            "database_uri_user": database_user,
            "database_uri_host": "citadel-postgres",
            "migration_user": migration_user,
            "redis_host": "citadel-redis",
            "minio_endpoint": "citadel-minio:9000",
            "origin": "https://citadel.example.test",
            "oauth_redirect": "https://citadel.example.test/api/auth/oauth",
            "api_previous_secrets": {},
        }, process


def test_existing_deployment_overrides_and_database_identity_remain_consistent(tmp_path):
    documents = _render(
        tmp_path,
        {
            "env": {
                "FRONTEND_BASE_URL": "https://legacy.example.test",
                "OAUTH_REDIRECT_BASE": "https://login.example.test/callback",
                "POSTGRES_USER": "custom_app",
                "POSTGRES_DB": "custom_db",
                "POSTGRES_HOST": "private-postgres",
                "REDIS_HOST": "private-redis",
                "MINIO_ENDPOINT": "private-storage:9000",
                "SANDBOX_K8S_NAMESPACE": "sandbox-team",
                "SANDBOX_HTTP_PROXY": "http://custom-proxy:3128",
                "OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS": "100",
            }
        },
    )
    api = _environment(_resource(documents, "Deployment", "citadel-api"))
    assert api["FRONTEND_BASE_URL"] == "https://legacy.example.test"
    assert api["OAUTH_REDIRECT_BASE"] == "https://login.example.test/callback"
    assert api["POSTGRES_HOST"] == "private-postgres"
    assert api["REDIS_HOST"] == "private-redis"
    assert api["MINIO_ENDPOINT"] == "private-storage:9000"
    assert api["SANDBOX_K8S_NAMESPACE"] == "sandbox-team"
    assert api["SANDBOX_HTTP_PROXY"] == "http://custom-proxy:3128"
    assert api["OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS"] == "100"
    postgres = _environment(_resource(documents, "StatefulSet", "citadel-postgres"))
    assert postgres["OPENCITADEL_APP_USER"] == api["POSTGRES_USER"] == "custom_app"
    assert postgres["POSTGRES_DB"] == api["POSTGRES_DB"] == "custom_db"


def test_chart_database_identity_values_apply_to_api_and_bootstrap(tmp_path):
    documents = _render(tmp_path, {"postgresql": {"user": "api_custom", "database": "db_custom"}})
    api = _environment(_resource(documents, "Deployment", "citadel-api"))
    postgres = _environment(_resource(documents, "StatefulSet", "citadel-postgres"))
    assert api["POSTGRES_USER"] == postgres["OPENCITADEL_APP_USER"] == "api_custom"
    assert api["POSTGRES_DB"] == postgres["POSTGRES_DB"] == "db_custom"


def test_fractional_legacy_shutdown_override_is_preserved(tmp_path):
    documents = _render(tmp_path, {"env": {"OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS": "95.5"}})
    api = _environment(_resource(documents, "Deployment", "citadel-api"))
    assert api["OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS"] == "95.5"


@pytest.mark.parametrize("enable_minio", [False, True])
def test_legacy_cos_upgrade_retains_storage_and_only_initializes_needed_minio(
    tmp_path, enable_minio
):
    legacy = {"env": {"COS_REGION": "ap-shanghai", "COS_BUCKET": "existing-bucket"}}
    first = _render(tmp_path, {**legacy, "minio": {"enabled": False}})
    application = _resource(first, "Secret", "citadel-secret")["stringData"]
    assert application["MINIO_ACCESS_KEY"] == application["MINIO_SECRET_KEY"] == ""
    chart, existing = _lookup_chart(tmp_path, first)
    second = _render(
        tmp_path,
        {**legacy, "fixtureSecrets": existing, "minio": {"enabled": enable_minio}},
        chart=chart,
        upgrade=True,
    )
    api = _environment(_resource(second, "Deployment", "citadel-api"))
    assert api["STORAGE_PROVIDER"] == "cos"
    after = _resource(second, "Secret", "citadel-secret")["stringData"]
    assert after["POSTGRES_PASSWORD"] == application["POSTGRES_PASSWORD"]
    if enable_minio:
        assert len(after["MINIO_ACCESS_KEY"]) >= 32
        assert len(after["MINIO_SECRET_KEY"]) >= 32
        assert _resource(second, "StatefulSet", "citadel-minio")
    else:
        assert after["MINIO_ACCESS_KEY"] == after["MINIO_SECRET_KEY"] == ""


def test_explicit_storage_provider_wins_over_legacy_cos_settings(tmp_path):
    documents = _render(
        tmp_path,
        {
            "env": {
                "STORAGE_PROVIDER": "minio",
                "COS_REGION": "ap-shanghai",
                "COS_BUCKET": "existing-bucket",
            }
        },
    )
    assert _environment(_resource(documents, "Deployment", "citadel-api"))["STORAGE_PROVIDER"] == (
        "minio"
    )


def test_upgrade_reuses_persisted_credentials_and_explicit_value_wins(tmp_path):
    first = _render(tmp_path)
    chart, existing = _lookup_chart(tmp_path, first)
    second = _render(tmp_path, {"fixtureSecrets": existing}, chart=chart, upgrade=True)
    for before in (document for document in first if document["kind"] == "Secret"):
        after = _resource(second, "Secret", before["metadata"]["name"])
        assert after["stringData"] == before["stringData"]
    override = "explicit-api-key-credential-" + "x" * 40
    third = _render(
        tmp_path,
        {"fixtureSecrets": existing, "secrets": {"apiKeySecret": override}},
        chart=chart,
        upgrade=True,
    )
    application = _resource(third, "Secret", "citadel-secret")["stringData"]
    assert application["API_KEY_SECRET"] == override
    assert (
        application["SESSION_SECRET"]
        == _resource(first, "Secret", "citadel-secret")["stringData"]["SESSION_SECRET"]
    )


def test_legacy_upgrade_preserves_database_hmac_fallback(tmp_path):
    first = _render(tmp_path)
    chart, existing = _lookup_chart(tmp_path, first)
    del existing["citadel-secret"]["data"]["DATABASE_AUTHORIZATION_SIGNING_SECRET"]
    del existing["citadel-secret"]["data"]["PUBLIC_CURSOR_SECRET"]
    del existing["citadel-secret"]["data"]["METRICS_TOKEN"]
    second = _render(tmp_path, {"fixtureSecrets": existing}, chart=chart, upgrade=True)
    application = _resource(second, "Secret", "citadel-secret")["stringData"]
    assert application["DATABASE_AUTHORIZATION_SIGNING_SECRET"] == application["SESSION_SECRET"]
    assert (
        application["PUBLIC_CURSOR_SECRET"]
        == hashlib.sha256(application["API_KEY_SECRET"].encode()).hexdigest()
    )
    assert application["METRICS_TOKEN"] == ""


def test_upgrade_preserves_admin_identity_and_signing_key_metadata(tmp_path):
    first = _render(tmp_path)
    chart, existing = _lookup_chart(tmp_path, first)
    metadata = {
        "BOOTSTRAP_ADMIN_EMAIL": "original-admin@example.test",
        "API_KEY_SECRET_ID": "old-api-key",
        "AUDIT_SIGNING_KEY_ID": "old-audit-key",
        "API_KEY_PREVIOUS_SECRETS": json.dumps({"previous": "retained-previous-key-" + "x" * 40}),
    }
    for key, value in metadata.items():
        existing["citadel-secret"]["data"][key] = base64.b64encode(value.encode()).decode()
    second = _render(tmp_path, {"fixtureSecrets": existing}, chart=chart, upgrade=True)
    application = _resource(second, "Secret", "citadel-secret")["stringData"]
    for key, value in metadata.items():
        assert application[key] == value


def test_upgrade_missing_durable_secret_fails_instead_of_rotating(tmp_path):
    error = _render(tmp_path, upgrade=True, succeeds=False)
    assert "refusing automatic rotation" in error


def test_secret_generation_is_cached_across_templates(tmp_path):
    chart = tmp_path / "chart"
    shutil.copytree(CHART, chart)
    probe = chart / "templates/cache-probe.yaml"
    probe.write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: cache-probe\ndata:\n"
        '  credential: {{ include "opencitadel.secretValue" (dict "root" . '
        '"group" "application" "key" "API_KEY_SECRET" "value" "" '
        '"generate" true "minimum" 32) | quote }}\n'
    )
    documents = _render(tmp_path, chart=chart)
    assert (
        _resource(documents, "ConfigMap", "cache-probe")["data"]["credential"]
        == _resource(documents, "Secret", "citadel-secret")["stringData"]["API_KEY_SECRET"]
    )


def test_existing_secret_references_keep_the_four_database_trust_domains(tmp_path):
    documents = _render(
        tmp_path,
        {
            "existingSecrets": {
                "application": "external-app",
                "postgresBootstrap": "external-postgres-bootstrap",
                "migration": "external-migration",
                "executionKernel": "external-kernel",
            }
        },
    )
    assert not any(document["kind"] == "Secret" for document in documents)
    api = _resource(documents, "Deployment", "citadel-api")["spec"]["template"]["spec"]
    assert api["containers"][0]["envFrom"][0]["secretRef"]["name"] == "external-app"
    migration = {item["name"]: item for item in api["initContainers"][0]["env"]}
    assert migration["POSTGRES_ADMIN_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == (
        "external-migration"
    )
    kernel = _environment(_resource(documents, "Deployment", "citadel-execution-kernel"))
    assert kernel["POSTGRES_PASSWORD"]["secretKeyRef"]["name"] == "external-kernel"
    postgres = _environment(_resource(documents, "StatefulSet", "citadel-postgres"))
    assert postgres["POSTGRES_PASSWORD"]["secretKeyRef"]["name"] == "external-postgres-bootstrap"
    assert postgres["OPENCITADEL_APP_PASSWORD"]["secretKeyRef"]["name"] == "external-app"


@pytest.mark.parametrize(
    "missing", ["SESSION_SECRET", "POSTGRES_PASSWORD", "BOOTSTRAP_ADMIN_PASSWORD"]
)
def test_existing_application_secret_missing_required_key_is_rejected(tmp_path, missing):
    first = _render(tmp_path)
    chart, existing = _lookup_chart(tmp_path, first)
    existing["external-app"] = existing.pop("citadel-secret")
    del existing["external-app"]["data"][missing]
    error = _render(
        tmp_path,
        {"existingSecrets": {"application": "external-app"}, "fixtureSecrets": existing},
        chart=chart,
        succeeds=False,
    )
    assert f"missing required key {missing}" in error


def test_ingress_origin_drives_cookies_and_oauth_without_repeated_values(tmp_path):
    documents = _render(
        tmp_path, {"publicUrl": "", "ingress": {"enabled": True, "host": "portal.example.test"}}
    )
    api = _environment(_resource(documents, "Deployment", "citadel-api"))
    assert api["FRONTEND_BASE_URL"] == "https://portal.example.test"
    assert api["OAUTH_REDIRECT_BASE"] == "https://portal.example.test/api/auth/oauth"
    assert api["COOKIE_SECURE"] == "true"


def test_local_http_chart_derives_non_secure_cookie(tmp_path):
    documents = _render(tmp_path, {"publicUrl": "", "env": {"ENV": "development"}})
    api = _environment(_resource(documents, "Deployment", "citadel-api"))
    assert api["FRONTEND_BASE_URL"] == "http://localhost:8088"
    assert api["COOKIE_SECURE"] == "false"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"publicUrl": ""}, "real production HTTPS origin"),
        ({"publicUrl": "http://portal.example.test"}, "must use HTTPS"),
        ({"secrets": {"postgresPassword": "weak"}}, "postgresPassword"),
        ({"secrets": {"auditSigningKey": "replace-with-" + "a" * 32}}, "auditSigningKey"),
        ({"env": {"COOKIE_SECURE": "false"}}, "COOKIE_SECURE=true"),
        (
            {"env": {"OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS": "130"}},
            "must exceed the effective positive shutdown timeout",
        ),
        ({"postgresql": {"enabled": False}}, "env.POSTGRES_HOST is required"),
        ({"minio": {"enabled": False}, "env": {"STORAGE_PROVIDER": "minio"}}, "external MinIO"),
    ],
)
def test_production_configuration_errors_fail_before_deployment(tmp_path, values, message):
    assert message in _render(tmp_path, values, succeeds=False)


def test_optional_service_tokens_are_generated_only_when_enabled(tmp_path):
    documents = _render(
        tmp_path,
        {
            "opsCollector": {"enabled": True},
            "opsActuator": {"enabled": True},
            "monitoring": {"serviceMonitor": {"enabled": True}},
        },
    )
    assert (
        len(
            _resource(documents, "Secret", "citadel-ops-collector-secret")["stringData"][
                "OPS_COLLECTOR_TOKEN"
            ]
        )
        >= 32
    )
    assert (
        len(
            _resource(documents, "Secret", "citadel-ops-actuator-secret")["stringData"][
                "OPS_ACTUATOR_TOKEN"
            ]
        )
        >= 32
    )
    assert (
        len(_resource(documents, "Secret", "citadel-secret")["stringData"]["METRICS_TOKEN"]) >= 32
    )


def test_upgrade_can_enable_optional_services_and_preserves_their_tokens(tmp_path):
    first = _render(tmp_path)
    chart, existing = _lookup_chart(tmp_path, first)
    enabled = {
        "opsCollector": {"enabled": True},
        "opsActuator": {"enabled": True},
        "monitoring": {"serviceMonitor": {"enabled": True}},
    }
    second = _render(tmp_path, {**enabled, "fixtureSecrets": existing}, chart=chart, upgrade=True)
    for document in second:
        if document["kind"] == "Secret":
            existing[document["metadata"]["name"]] = {
                "data": {
                    key: base64.b64encode(value.encode()).decode()
                    for key, value in document["stringData"].items()
                }
            }
    third = _render(tmp_path, {**enabled, "fixtureSecrets": existing}, chart=chart, upgrade=True)
    for name, key in (
        ("citadel-ops-collector-secret", "OPS_COLLECTOR_TOKEN"),
        ("citadel-ops-actuator-secret", "OPS_ACTUATOR_TOKEN"),
        ("citadel-secret", "METRICS_TOKEN"),
    ):
        generated = _resource(second, "Secret", name)["stringData"][key]
        assert len(generated) >= 32
        assert _resource(third, "Secret", name)["stringData"][key] == generated
