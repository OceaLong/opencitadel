#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Helpers for grounding user context in agent prompts."""
from app.domain.models.message import Message


def has_user_attachments(message: Message) -> bool:
    return bool(message.attachments or message.vision_attachments)


def format_user_attachments_for_prompt(message: Message, *, locale: str) -> str:
    if not has_user_attachments(message):
        return "（无）" if locale == "zh" else "(none)"
    lines = list(message.attachments)
    for attachment in message.vision_attachments:
        label = attachment.ref_url or attachment.mime_type or "media"
        lines.append(label)
    return "\n".join(lines)
