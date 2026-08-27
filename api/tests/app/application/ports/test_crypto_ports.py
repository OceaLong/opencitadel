from pathlib import Path

from app.application.ports.crypto import (
    PasswordHashPort,
    SecretEnvelopePort,
    ServiceKeyPort,
    TokenCodecPort,
    VersionedSecretCipher,
)
from app.infrastructure.adapters.security_ports import (
    FernetSecretEnvelopeAdapter,
    JwtTokenCodecAdapter,
)
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.jwt_service import JwtService
from app.infrastructure.security.password_hasher import PasswordHasher
from app.infrastructure.security.service_api_key import ServiceApiKeyHasher


def test_crypto_ports_are_runtime_checkable() -> None:
    cipher = ApiKeyCipher("x" * 32)

    assert isinstance(PasswordHasher(), PasswordHashPort)
    assert isinstance(JwtTokenCodecAdapter(JwtService("x" * 32)), TokenCodecPort)
    assert isinstance(ServiceApiKeyHasher(), ServiceKeyPort)
    assert isinstance(cipher, VersionedSecretCipher)
    assert isinstance(FernetSecretEnvelopeAdapter(cipher), SecretEnvelopePort)


def test_secret_envelope_encrypts_only_sensitive_values() -> None:
    adapter = FernetSecretEnvelopeAdapter(ApiKeyCipher("x" * 32))

    mapping = adapter.encrypt_mapping({"Authorization": "Bearer secret", "region": "cn"})
    url = adapter.encrypt_url("https://user:password@example.test/path?token=secret")

    assert mapping.scheme == "fernet_v2"
    assert mapping.value is not None
    assert mapping.value["Authorization"] != "Bearer secret"
    assert mapping.value["region"] == "cn"
    assert url.scheme == "fernet_v2"
    assert url.value != "https://user:password@example.test/path?token=secret"


def test_crypto_consumers_do_not_import_security_or_settings_implementations() -> None:
    service_root = Path(__file__).resolve().parents[4] / "app/application/services"
    names = (
        "auth_service.py",
        "bootstrap_service.py",
        "team_service.py",
        "service_api_key_service.py",
        "inference_endpoint_service.py",
        "integration_server_service.py",
        "scheduled_job_service.py",
        "patrol_run_service.py",
    )
    sources = "\n".join((service_root / name).read_text() for name in names)

    assert "app.infrastructure.security" not in sources
    assert "core.config" not in sources
