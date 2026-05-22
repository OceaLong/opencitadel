#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List

from pydantic import BaseModel, Field


class VisionAttachment(BaseModel):
    """多模态图片附件，用于直接传给支持视觉理解的模型。"""
    mime_type: str = ""
    data_base64: str = ""
    ref_url: str = ""


class Message(BaseModel):
    """用户传递的消息"""
    message: str = ""  # 用户发送的消息
    attachments: List[str] = Field(default_factory=list)  # 用户发送的附件（沙箱路径）
    vision_attachments: List[VisionAttachment] = Field(default_factory=list)
