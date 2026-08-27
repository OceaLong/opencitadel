import pytest

from app.infrastructure.security.api_key_cipher import ApiKeyCipher, ApiKeyCipherError


def test_mask_hides_middle_of_key():
    assert ApiKeyCipher.mask("sk-abcdefghijklmnop") == "sk-a****mnop"


def test_versioned_ciphertext_uses_current_key_and_reads_previous_key():
    old_cipher = ApiKeyCipher("o" * 32, key_id="old")
    old_value = old_cipher.encrypt_versioned("sk-rotating")
    rotating_cipher = ApiKeyCipher(
        "n" * 32,
        key_id="current",
        previous_secrets={"old": "o" * 32},
    )

    assert rotating_cipher.decrypt_versioned(old_value) == "sk-rotating"
    assert rotating_cipher.key_id_from_ciphertext(old_value) == "old"
    assert (
        rotating_cipher.key_id_from_ciphertext(rotating_cipher.encrypt_versioned("sk-new"))
        == "current"
    )


def test_versioned_ciphertext_rejects_unknown_key_id():
    encrypted = ApiKeyCipher(
        "o" * 32,
        key_id="retired",
    ).encrypt_versioned("sk-retired")

    with pytest.raises(ApiKeyCipherError, match="key id"):
        ApiKeyCipher("n" * 32, key_id="current").decrypt_versioned(encrypted)
