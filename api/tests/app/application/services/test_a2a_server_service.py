#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.application.services.a2a_server_service import (
    build_a2a_text_response,
    extract_text_from_a2a_params,
)


def test_extract_text_from_a2a_params():
    params = {
        "message": {
            "parts": [
                {"kind": "text", "text": "hello"},
                {"kind": "text", "text": "world"},
            ],
        },
    }
    assert extract_text_from_a2a_params(params) == "hello\nworld"


def test_build_a2a_text_response():
    response = build_a2a_text_response("req-1", "done")
    assert response["id"] == "req-1"
    assert response["result"]["message"]["parts"][0]["text"] == "done"
