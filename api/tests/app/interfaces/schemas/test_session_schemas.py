from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.interfaces.schemas.session import ChatRequest


@pytest.mark.parametrize("message", ["", " ", "\n\t"])
def test_chat_request_rejects_blank_messages(message: str) -> None:
    with pytest.raises(ValidationError, match="message must not be blank"):
        ChatRequest(message=message, request_id=uuid4())


def test_chat_request_normalizes_message_boundaries() -> None:
    request = ChatRequest(message="  inspect the deployment  ", request_id=uuid4())

    assert request.message == "inspect the deployment"


def test_chat_resume_rejects_turn_request_id() -> None:
    with pytest.raises(
        ValidationError,
        match="request_id is only valid when message is present",
    ):
        ChatRequest(request_id=uuid4(), event_id="cursor-1")
