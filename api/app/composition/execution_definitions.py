"""Code-owned baseline; release manifests identify actual source artifacts."""

import hashlib
from pathlib import Path

from app.application.execution import activity_types
from app.application.execution.context_compiler import compile_context
from app.application.execution.decisions import DECISION_PLANNERS
from app.application.execution.definition_registry import DefinitionRegistry
from app.domain.execution.definition import (
    DefinitionManifest,
    HandlerImplementation,
    SchemaCompatibility,
    WorkerCapabilities,
)
from app.domain.execution.family import RunFamily
from app.domain.execution.run import RunAggregate
from app.domain.runtime_policy.context import ContextPolicy


def build_artifact_digest() -> str:
    root = Path(__file__).resolve().parents[2]
    paths = [*root.joinpath("app").rglob("*.py"), *root.joinpath("core").rglob("*.py")]
    paths.extend(path for path in (root / "pyproject.toml", root / "uv.lock") if path.is_file())
    if not paths:
        raise RuntimeError("execution code artifact is unavailable")
    digest = hashlib.sha256()
    for path in sorted(paths):
        name = path.relative_to(root).as_posix().encode()
        body = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest()


def build_code_definitions() -> DefinitionRegistry:
    aggregate = RunAggregate()

    def schemas(registry):
        return tuple(
            SchemaCompatibility(
                name=name,
                minimum=1,
                maximum=registry.latest_version(name),
                write_version=registry.latest_version(name),
            )
            for name in sorted(registry.registered_names())
        )

    artifact = build_artifact_digest()
    registry = DefinitionRegistry()
    for family, spec in DECISION_PLANNERS.items():
        value = DefinitionManifest(
            family=family,
            definition_version=1,
            planner_version=f"{family.value}.v1",
            compiler_version="context.v1",
            context_protocol_version=1,
            handlers=tuple(
                HandlerImplementation(activity_type=name, handler_version="1")
                for name in sorted(spec.emits_activity_types)
            ),
            command_schemas=schemas(aggregate.command_registry),
            event_schemas=schemas(aggregate.event_registry),
            build_artifact_digest=artifact,
            context_policy=ContextPolicy(),
        )
        registry.register(value, planner=spec.planner, compiler=compile_context)
        if family in {RunFamily.AGENT, RunFamily.ASK}:
            # Both implementations remain addressable. The planner's exact pin
            # selects legacy round semantics or durable context/final-answer semantics.
            contextual = value.model_copy(
                update={
                    "definition_version": 2,
                    "planner_version": f"{family.value}.v2",
                    "summary_purposes": ("production", "evaluation_subject"),
                    # The release accepts evidence beyond three summary windows;
                    # each of these bounded chunks still gets its own permit.
                    "context_policy": ContextPolicy(compactions_per_round=4),
                    "handlers": tuple(
                        sorted(
                            (
                                *value.handlers,
                                HandlerImplementation(
                                    activity_type=activity_types.CONTEXT_PREPARE,
                                    handler_version="1",
                                ),
                                HandlerImplementation(
                                    activity_type=activity_types.CONTEXT_COMPACT,
                                    handler_version="1",
                                ),
                            ),
                            key=lambda handler: handler.activity_type,
                        )
                    ),
                }
            )
            contextual = DefinitionManifest.model_validate(contextual.model_dump(mode="json"))
            registry.register(contextual, planner=spec.planner, compiler=compile_context)
    return registry


def initial_definition_heads(registry):
    """Choose the code release's initial heads; never rewrite an existing head."""
    heads = {}
    for manifest in registry.manifests:
        previous = heads.get(manifest.family)
        if previous is None or manifest.definition_version > previous.definition_version:
            heads[manifest.family] = manifest
    return tuple(heads[family] for family in sorted(heads))


def worker_capabilities(registry: DefinitionRegistry, *, worker_id, boot_id):
    manifests = registry.manifests
    artifacts = {value.build_artifact_digest for value in manifests}
    if len(artifacts) != 1:
        raise ValueError("baseline definitions must bind one actual build artifact")
    return WorkerCapabilities(
        worker_id=worker_id,
        boot_id=boot_id,
        build_artifact_digest=next(iter(artifacts)),
        definitions=tuple(value.pin for value in manifests),
        handlers=tuple(
            sorted(
                {item for value in manifests for item in value.handlers},
                key=lambda item: (item.activity_type, item.handler_version),
            )
        ),
        compilers=tuple(sorted({value.compiler_version for value in manifests})),
        command_schemas=manifests[0].command_schemas,
        event_schemas=manifests[0].event_schemas,
    )
