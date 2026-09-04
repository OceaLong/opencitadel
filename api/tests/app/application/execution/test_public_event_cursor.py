"""Public-event cursor signing key rotation (K4-5).

New cursors are always signed with the current secret; decoding additionally
accepts cursors signed with any configured previous secret so in-flight
clients survive a runtime rotation window.
"""

import pytest

from app.application.execution.public_projection import PublicEventCursor

_OLD = b"old-secret-0123456789abcdef!!"
_NEW = b"new-secret-0123456789abcdef!!"


def test_decode_accepts_current_and_previous_secret_cursors() -> None:
    old_codec = PublicEventCursor(secret=_OLD)
    rotated = PublicEventCursor(secret=_NEW, previous_secrets=(_OLD,))

    legacy_cursor = old_codec.encode(42)
    fresh_cursor = rotated.encode(43)

    assert rotated.decode(legacy_cursor) == 42
    assert rotated.decode(fresh_cursor) == 43
    # New cursors are signed with the *new* secret only.
    with pytest.raises(ValueError, match="invalid public event cursor"):
        old_codec.decode(fresh_cursor)


def test_decode_rejects_cursors_signed_with_an_unknown_secret() -> None:
    rotated = PublicEventCursor(secret=_NEW, previous_secrets=(_OLD,))
    stranger = PublicEventCursor(secret=b"other-secret-0123456789abcdef")

    with pytest.raises(ValueError, match="invalid public event cursor"):
        rotated.decode(stranger.encode(7))


def test_previous_secrets_must_meet_the_minimum_length() -> None:
    with pytest.raises(ValueError, match="at least 16 bytes"):
        PublicEventCursor(secret=_NEW, previous_secrets=(b"short",))
