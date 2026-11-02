from app.composition.execution_definitions import build_code_definitions, worker_capabilities
from app.domain.execution.family import RunFamily


def test_code_manifests_have_actual_artifact_and_complete_schema_closure():
    registry = build_code_definitions()
    assert {value.family for value in registry.manifests} == set(RunFamily)
    digests = {value.build_artifact_digest for value in registry.manifests}
    assert len(digests) == 1
    assert next(iter(digests)) != "a" * 64
    capabilities = worker_capabilities(registry, worker_id="test-worker", boot_id="test-boot")
    assert all(capabilities.supports(value) for value in registry.manifests)
    assert all(registry.resolve(value.pin).manifest == value for value in registry.manifests)
    commands = {item.name: item.write_version for item in registry.manifests[0].command_schemas}
    assert commands["CreateRun"] == 3
    assert commands["RequestActivity"] == 4


def test_initial_heads_enable_context_and_retain_legacy_round_semantics():
    from app.application.execution.decisions.context import context_enabled
    from app.composition.execution_definitions import initial_definition_heads

    registry = build_code_definitions()
    heads = {value.family: value for value in initial_definition_heads(registry)}
    for family in RunFamily:
        versions = [value for value in registry.manifests if value.family == family]
        if family in {RunFamily.AGENT, RunFamily.ASK}:
            assert {value.definition_version for value in versions} == {1, 2}
            assert heads[family].definition_version == 2
            assert context_enabled(heads[family])
            assert heads[family].context_policy.compactions_per_round == 4
            assert not context_enabled(next(v for v in versions if v.definition_version == 1))
        else:
            assert len(versions) == 1
            assert heads[family].definition_version == 1
            assert not context_enabled(heads[family])


def test_every_release_manifest_roundtrips_without_changing_its_digest():
    from app.domain.execution.definition import DefinitionManifest

    for value in build_code_definitions().manifests:
        restored = DefinitionManifest.model_validate(value.model_dump(mode="json"))
        assert restored.pin == value.pin
        if value.definition_version == 2:
            assert set(value.summary_purposes) == {"production", "evaluation_subject"}
