from app.domain.models.session_mode import SessionMode


def test_session_mode_is_a_standalone_closed_enum() -> None:
    assert [mode.value for mode in SessionMode] == ["ask", "agent"]
