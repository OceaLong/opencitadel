from uuid import uuid4


def test_private_case_input_uses_actual_runtime_fields_and_fixed_prompt():
    from app.application.evaluation.scheduler import private_case_input
    from app.domain.evaluation.configuration import ConfigSelection, ConfigVersion
    from app.domain.evaluation.dataset import CaseRevision, ConversationMessage

    case = CaseRevision(
        case_key="case",
        input="Question",
        history=(ConversationMessage(role="user", content="Earlier"),),
    )
    config = ConfigVersion(
        id=uuid4(),
        entity_id=uuid4(),
        revision=1,
        name="fixed",
        selection=ConfigSelection(model_id="m", mode="ask", prompt="Be concise", temperature=0.2),
        fingerprint="f",
        snapshot={},
    )
    payload = private_case_input(case, config, ())
    assert payload["message"] == "Be concise\n\nQuestion"
    assert payload["conversation"] == [{"role": "user", "content": "Earlier"}]
    assert payload["mode"] == "ask"
    assert payload["temperature_override"] == 0.2
    assert "history" not in payload


def test_recording_selection_uses_fixed_configuration_reference():
    from types import SimpleNamespace

    from app.application.evaluation.scheduler import recording_for_configuration

    first, selected = uuid4(), uuid4()
    suite = SimpleNamespace(recording_versions=(first, selected))
    config = SimpleNamespace(
        selection=SimpleNamespace(
            external_contract_ref=SimpleNamespace(kind="recording", version_id=selected)
        )
    )
    assert recording_for_configuration(suite, config) == selected


def test_fixed_case_history_over_100_keeps_revision_identity_and_explicit_limits():
    from types import SimpleNamespace

    import pytest

    from app.application.evaluation.scheduler import private_case_input
    from app.domain.evaluation.dataset import CaseRevision, ConversationMessage

    history = tuple(
        ConversationMessage(role="user", content=f"fact {index}") for index in range(150)
    )
    case = CaseRevision(case_key="long", input="last question", history=history)
    config = SimpleNamespace(
        id=uuid4(),
        selection=SimpleNamespace(
            prompt="", resources=(), mode="ask", model_id="m", skill_id=None, temperature=0.2
        ),
    )
    value = private_case_input(case, config, ())
    assert len(value["conversation"]) == 150
    assert value["conversation"][0]["content"] == "fact 0"
    assert value["_context_dataset"] == {
        "case_revision_id": str(case.id),
        "case_revision": 1,
        "config_version_id": str(config.id),
    }
    with pytest.raises(ValueError, match="case_conversation_limit"):
        private_case_input(case.model_copy(update={"history": history[:1] * 4097}), config, ())
    with pytest.raises(ValueError, match="case_conversation_bytes_limit"):
        private_case_input(
            case.model_copy(
                update={"history": (ConversationMessage(role="user", content="x" * 8388608),)}
            ),
            config,
            (),
        )
