#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.message import Message, VisionAttachment
from app.domain.utils.prompt_context import (
    format_user_attachments_for_prompt,
    has_user_attachments,
)


def test_has_user_attachments_false_for_empty_message():
    message = Message(message="hello")
    assert has_user_attachments(message) is False


def test_has_user_attachments_true_for_paths():
    message = Message(message="hello", attachments=["/workspace/a.png"])
    assert has_user_attachments(message) is True


def test_has_user_attachments_true_for_vision_only():
    message = Message(
        message="hello",
        vision_attachments=[VisionAttachment(mime_type="image/png", data_base64="aW1n")],
    )
    assert has_user_attachments(message) is True


def test_format_user_attachments_for_prompt_none_zh():
    message = Message(message="hello")
    assert format_user_attachments_for_prompt(message, locale="zh") == "（无）"


def test_format_user_attachments_for_prompt_none_en():
    message = Message(message="hello")
    assert format_user_attachments_for_prompt(message, locale="en") == "(none)"


def test_format_user_attachments_for_prompt_with_paths():
    message = Message(
        message="hello",
        attachments=["/workspace/report.pdf", "/workspace/photo.jpg"],
    )
    assert format_user_attachments_for_prompt(message, locale="zh") == (
        "/workspace/report.pdf\n/workspace/photo.jpg"
    )


def test_format_user_attachments_for_prompt_with_vision_labels():
    message = Message(
        message="hello",
        attachments=["/workspace/doc.txt"],
        vision_attachments=[
            VisionAttachment(mime_type="image/png", ref_url="cos://bucket/img.png"),
            VisionAttachment(mime_type="audio/mpeg"),
        ],
    )
    assert format_user_attachments_for_prompt(message, locale="en") == (
        "/workspace/doc.txt\ncos://bucket/img.png\naudio/mpeg"
    )
