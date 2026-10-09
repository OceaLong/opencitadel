"""Deployment-owned inventory construction shared by API, kernel and broker."""


def build_environment_registry(settings, *, broker=False, request_observer=None):
    """Restart-bound administrator file is the provenance authority, never an HTTP body."""
    import json
    from functools import partial
    from pathlib import Path

    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.environment import TestCredentialRef, TestTarget
    from app.infrastructure.adapters.evaluation_environment import DockerNetworkEnvironmentAdapter
    from app.infrastructure.adapters.evaluation_external import (
        RegisteredHTTPTestExecutor,
        SyntheticTestCredentialResolver,
    )

    path = settings.evaluation_test_inventory_path
    if not path:
        return AdapterRegistry()
    body = json.loads(Path(path).read_text())
    if (
        set(body)
        - {"schema_version", "images", "fixture_image", "bootstrap_image", "targets", "credentials"}
        or body.get("schema_version") != 1
    ):
        raise ValueError("test_inventory_schema_invalid")
    if not settings.evaluation_local_docker_enabled or settings.env == "production":
        raise ValueError("local_test_environment_deployment_forbidden")
    targets = tuple(TestTarget.model_validate(value) for value in body.get("targets", ()))
    credentials = tuple(
        TestCredentialRef.model_validate(value) for value in body.get("credentials", ())
    )
    resolver = SyntheticTestCredentialResolver()
    if any(
        c.locator != "synthetic-e04-v1" or c.resolver != "synthetic-e04-v1" for c in credentials
    ):
        raise ValueError("local_fixture_credentials_forbidden")
    if broker:
        if not settings.sandbox_broker_url or not settings.sandbox_broker_token:
            raise ValueError("environment_broker_required")
        from app.infrastructure.evaluation.broker_adapter import BrokerEnvironmentAdapter

        adapter_type = partial(
            BrokerEnvironmentAdapter,
            broker_url=settings.sandbox_broker_url,
            broker_token=settings.sandbox_broker_token,
            request_observer=request_observer,
        )
    else:
        adapter_type = DockerNetworkEnvironmentAdapter
    adapter = adapter_type(
        allowed_images=body["images"],
        fixture_image=body["fixture_image"],
        bootstrap_image=body["bootstrap_image"],
        local_content_ids=True,
        acceptance_owner=(
            settings.sandbox_labels["com.opencitadel.acceptance.project"],
            settings.sandbox_labels["com.opencitadel.acceptance.run"],
        )
        if getattr(settings, "evaluation_acceptance_enabled", False)
        else None,
    )
    executors = {
        target.id: RegisteredHTTPTestExecutor(target, credential_resolver=resolver)
        for target in targets
        if target.kind in {"mcp", "a2a"}
    }
    return AdapterRegistry(
        adapters={"docker-http-cell-v1": adapter},
        targets=targets,
        credentials=credentials,
        credential_resolvers={"synthetic-e04-v1": resolver},
        executors=executors,
    )
