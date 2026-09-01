"""Domain enrichment for the session <-> execution Run binding state machine."""

from uuid import uuid4

from app.domain.models.session import Session


def test_attach_run_records_active_run_identity() -> None:
    session = Session(owner_user_id="user-1")
    run_id = uuid4()
    request_id = uuid4()

    returned = session.attach_run(run_id, request_id)

    assert returned is session
    assert session.active_execution_run_id == run_id
    assert session.active_execution_request_id == request_id


def test_attach_run_does_not_touch_status() -> None:
    # status is projector-owned (derived from the event stream); attach must not
    # write it, to avoid competing with the projector on session.status.
    session = Session(owner_user_id="user-1")
    original_status = session.status

    session.attach_run(uuid4(), uuid4())

    assert session.status == original_status


def test_release_run_clears_active_run_identity() -> None:
    session = Session(owner_user_id="user-1")
    session.attach_run(uuid4(), uuid4())

    returned = session.release_run()

    assert returned is session
    assert session.active_execution_run_id is None
    assert session.active_execution_request_id is None


def test_release_run_is_idempotent_when_no_run_attached() -> None:
    session = Session(owner_user_id="user-1")

    session.release_run()

    assert session.active_execution_run_id is None
    assert session.active_execution_request_id is None
