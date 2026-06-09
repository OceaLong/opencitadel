#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64

from app.domain.utils.vision_tokens import (
    count_message_image_stats,
    estimate_image_tokens,
    estimate_messages_tokens,
)


def test_estimate_image_tokens_low_detail():
    assert estimate_image_tokens(512, 512, detail="low") == 85


def test_estimate_image_tokens_high_detail():
    tokens = estimate_image_tokens(1024, 1024, detail="high")
    assert tokens > 85


def test_count_message_image_stats_data_url():
    png_b64 = base64.b64encode(b"\x89PNG" + b"x" * 100).decode("ascii")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
        ],
    }]
    count, total_bytes, vision_tokens = count_message_image_stats(messages)
    assert count == 1
    assert total_bytes > 0
    assert vision_tokens > 0


def test_estimate_messages_tokens_includes_vision():
    png_b64 = base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 200,
    ).decode("ascii")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
        ],
    }]
    tokens = estimate_messages_tokens(messages)
    assert tokens > 10
