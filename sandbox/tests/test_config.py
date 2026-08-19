from app.core.config import Settings


def test_server_timeout_uses_only_current_environment_name(monkeypatch):
    monkeypatch.delenv("SERVER_TIMEOUT_MINUTES", raising=False)
    monkeypatch.setenv("SERVICE_TIMEOUT_MINUTES", "5")

    assert Settings(_env_file=None).server_timeout_minutes == 60

    monkeypatch.setenv("SERVER_TIMEOUT_MINUTES", "7")
    assert Settings(_env_file=None).server_timeout_minutes == 7
