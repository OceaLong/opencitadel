from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.execution.view_cursor import InvalidViewCursor, ViewCursor
from app.domain.models.playback import PlaybackBoundary


def make_boundary(run_id=None):
    return PlaybackBoundary(
        run_id=run_id or uuid4(),
        formal_position=17,
        progress_position=4,
        observed_order=21,
        projection_revision=21,
        observed_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        projector_version=1,
    )


def decode(codec, token, b, *, include_cut=True, **overrides):
    expected = {
        "expected_run_id": b.run_id,
        "expected_scope_key": "user:u1",
        "expected_query_digest": "query-sha256",
        "expected_projector_version": 1,
    }
    if include_cut:
        expected.update(expected_projection_revision=21, expected_observed_order=21)
    expected.update(overrides)
    return codec.decode(token, **expected)


def test_cursor_round_trip_is_opaque_and_binds_all_context():
    codec = ViewCursor(secret=b"new-secret-is-long-enough")
    b = make_boundary()
    token = codec.encode(b, "user:u1", "query-sha256")
    assert str(b.run_id) not in token
    assert decode(codec, token, b) == b


def test_historical_cursor_authenticates_before_optional_live_cut_check():
    codec = ViewCursor(secret=b"new-secret-is-long-enough")
    historical = make_boundary()
    token = codec.encode(historical, "user:u1", "query-sha256")

    # A later live observation may advance to order/revision 22. Historical
    # decoding authenticates fixed route context first; persistence validates
    # the signed order-21 boundary separately.
    assert decode(codec, token, historical, include_cut=False) == historical
    with pytest.raises(InvalidViewCursor):
        decode(
            codec,
            token,
            historical,
            expected_projection_revision=22,
            expected_observed_order=22,
        )


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("expected_run_id", uuid4()),
        ("expected_scope_key", "team:t2"),
        ("expected_query_digest", "another-query"),
        ("expected_projector_version", 2),
        ("expected_projection_revision", 22),
        ("expected_observed_order", 20),
    ],
)
def test_cursor_rejects_cross_context_and_stale_cuts(override, value):
    codec = ViewCursor(secret=b"new-secret-is-long-enough")
    b = make_boundary()
    token = codec.encode(b, "user:u1", "query-sha256")
    with pytest.raises(InvalidViewCursor):
        decode(codec, token, b, **{override: value})


def test_cursor_rejects_bad_signature_and_unsupported_envelope_version():
    b = make_boundary()
    token = ViewCursor(secret=b"new-secret-is-long-enough").encode(b, "user:u1", "query-sha256")
    with pytest.raises(InvalidViewCursor):
        decode(ViewCursor(secret=b"other-secret-long-enough"), token, b)
    with pytest.raises(InvalidViewCursor):
        decode(ViewCursor(secret=b"new-secret-is-long-enough", envelope_version=2), token, b)


def test_old_key_decodes_during_rotation_but_new_tokens_use_current_key():
    old = b"old-secret-is-long-enough"
    new = b"new-secret-is-long-enough"
    b = make_boundary()
    old_token = ViewCursor(secret=old).encode(b, "user:u1", "query-sha256")
    rotating = ViewCursor(secret=new, previous_secrets=(old,))
    assert decode(rotating, old_token, b) == b
    new_token = rotating.encode(b, "user:u1", "query-sha256")
    with pytest.raises(InvalidViewCursor):
        decode(ViewCursor(secret=old), new_token, b)
