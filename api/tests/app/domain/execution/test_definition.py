import pytest
from pydantic import ValidationError

from app.application.execution.definition_registry import DefinitionRegistry
from app.domain.execution.definition import (
    DefinitionIntegrityError,
    DefinitionManifest,
    DefinitionUnavailable,
    HandlerImplementation,
    SchemaCompatibility,
    WorkerCapabilities,
)


def manifest(version=1, **changes):
    return DefinitionManifest(
        **{
            "family": "agent",
            "definition_version": version,
            "planner_version": f"agent.v{version}",
            "compiler_version": "context.v1",
            "context_protocol_version": 1,
            "handlers": (
                HandlerImplementation(activity_type="tool.call", handler_version=str(version)),
            ),
            "command_schemas": (
                SchemaCompatibility(name="CreateRun", minimum=1, maximum=2, write_version=2),
            ),
            "event_schemas": (
                SchemaCompatibility(name="RunCreated", minimum=1, maximum=2, write_version=2),
            ),
            "build_artifact_digest": "a" * 64,
            **changes,
        }
    )


def test_v04_same_identity_cannot_replace_code_or_digest():
    registry = DefinitionRegistry()
    planner, compiler = lambda *args: "v1", lambda *args: "compiled"
    original = manifest()
    registry.register(original, planner=planner, compiler=compiler)
    registry.register(original, planner=planner, compiler=compiler)
    with pytest.raises(DefinitionIntegrityError):
        registry.register(
            manifest(build_artifact_digest="b" * 64), planner=planner, compiler=compiler
        )
    with pytest.raises(DefinitionIntegrityError):
        registry.register(original, planner=lambda *args: "different", compiler=compiler)
    assert registry.resolve(original.pin).planner() == "v1"


def test_exact_version_and_digest_never_fall_back_to_latest():
    registry = DefinitionRegistry()
    for version in (1, 2):
        registry.register(
            manifest(version),
            planner=lambda *args, value=version: value,
            compiler=lambda *args: None,
        )
    assert registry.resolve(manifest(1).pin).planner() == 1
    assert registry.resolve(manifest(2).pin).planner() == 2
    with pytest.raises(DefinitionUnavailable):
        registry.resolve(manifest(3).pin)
    with pytest.raises(DefinitionIntegrityError):
        registry.resolve(manifest(build_artifact_digest="b" * 64).pin)


def test_manifest_digest_is_order_independent_but_binds_every_handler():
    one = HandlerImplementation(activity_type="model.call", handler_version="1")
    two = HandlerImplementation(activity_type="tool.call", handler_version="1")
    a = manifest(handlers=(one, two), summary_purposes=("production", "evaluation_subject"))
    b = manifest(handlers=(two, one), summary_purposes=("evaluation_subject", "production"))
    assert a.digest == b.digest
    assert (
        a.digest != manifest(handlers=(one, two.model_copy(update={"handler_version": "2"}))).digest
    )
    with pytest.raises(ValidationError):
        a.handlers[0].handler_version = "changed"
    with pytest.raises(ValidationError):
        manifest(handlers=(one, one))


def test_worker_needs_exact_definition_handler_and_compiler_closure():
    definition = manifest()
    caps = WorkerCapabilities(
        worker_id="worker",
        boot_id="boot",
        build_artifact_digest="c" * 64,
        definitions=(definition.pin,),
        handlers=definition.handlers,
        compilers=(definition.compiler_version,),
        command_schemas=definition.command_schemas,
        event_schemas=definition.event_schemas,
    )
    assert caps.supports(definition)
    assert not caps.supports(manifest(2))
    assert not caps.model_copy(update={"handlers": ()}).supports(definition)
    assert not caps.model_copy(update={"compilers": ()}).supports(definition)


def test_schema_ranges_cannot_claim_unsupported_writes():
    with pytest.raises(ValidationError):
        SchemaCompatibility(name="CreateRun", minimum=2, maximum=3, write_version=1)


def test_old_manifest_does_not_acquire_default_context_policy_or_change_its_digest():
    value = manifest()
    body = value.model_dump(mode="json")
    assert "context_policy" not in body
    assert DefinitionManifest.model_validate(body).digest == value.digest
    assert DefinitionManifest.model_validate(body).context_policy is None
    with pytest.raises(ValidationError, match="attested immutable context policy"):
        manifest(
            handlers=(HandlerImplementation(activity_type="context.prepare", handler_version="1"),)
        )
