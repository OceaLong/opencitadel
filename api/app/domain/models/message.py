#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List

from pydantic import BaseModel, Field

from app.domain.models.multimodal import MediaAttachment

# 向后兼容别名
VisionAttachment = MediaAttachment


class Message(BaseModel):
    """用户传递的消息"""
    message: str = ""  # 用户发送的消息
    attachments: List[str] = Field(default_factory=list)  # 用户发送的附件（沙箱路径）
    vision_attachments: List[MediaAttachment] = Field(default_factory=list)
