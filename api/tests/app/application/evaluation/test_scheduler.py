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
