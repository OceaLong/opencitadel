import jwt
import pytest

from app.infrastructure.security.jwt_service import JwtService

_OLD_SECRET = "old-jwt-secret-value-for-rotation-tests"
_NEW_SECRET = "new-jwt-secret-value-for-rotation-tests"


def test_token_signed_by_retired_secret_still_decodes_while_listed() -> None:
    old_service = JwtService(secret=_OLD_SECRET)
    token = old_service.issue_access_token(user_id="u1", role="member", token_version=1)

    # After rotating to a new current secret, the retired secret is listed as a
    # previous key so tokens it signed keep decoding until they expire.
    rotated = JwtService(
        secret=_NEW_SECRET,
        previous_secrets={"retired-1": _OLD_SECRET},
    )

    claims = rotated.decode(token, expected_type="access")
    assert claims["sub"] == "u1"
    assert claims["role"] == "member"


def test_token_signed_by_unlisted_secret_is_rejected() -> None:
    old_service = JwtService(secret=_OLD_SECRET)
    token = old_service.issue_access_token(user_id="u1", role="member", token_version=1)

    # No previous secrets => the retired secret is not accepted.
    rotated = JwtService(secret=_NEW_SECRET)

    with pytest.raises(jwt.InvalidSignatureError):
        rotated.decode(token)


def test_encode_always_uses_current_secret() -> None:
    service = JwtService(
        secret=_NEW_SECRET,
        previous_secrets={"retired-1": _OLD_SECRET},
    )
    token = service.issue_refresh_token(user_id="u2", token_version=3)

    # A token freshly issued must verify under the current secret alone.
    current_only = JwtService(secret=_NEW_SECRET)
    claims = current_only.decode(token, expected_type="refresh")
    assert claims["sub"] == "u2"
    assert claims["ver"] == 3


def test_current_secret_is_tried_first_and_previous_are_optional() -> None:
    service = JwtService(secret=_NEW_SECRET)
    token = service.issue_access_token(user_id="u3", role="admin", token_version=0)
    assert service.decode(token, expected_type="access")["sub"] == "u3"


def test_previous_secret_equal_to_current_is_ignored() -> None:
    service = JwtService(
        secret=_NEW_SECRET,
        previous_secrets={"dup": _NEW_SECRET},
    )
    assert service._decode_secrets == [_NEW_SECRET]


def test_invalid_previous_key_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="key id"):
        JwtService(secret=_NEW_SECRET, previous_secrets={"bad id!": _OLD_SECRET})


def test_empty_previous_secret_is_rejected() -> None:
    with pytest.raises(ValueError, match="未配置密钥"):
        JwtService(secret=_NEW_SECRET, previous_secrets={"retired-1": ""})
