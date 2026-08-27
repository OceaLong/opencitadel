from pydantic import BaseModel, Field

from app.domain.models.multimodal import MediaAttachment


class Message(BaseModel):
    """用户传递的消息"""

    message: str = ""  # 用户发送的消息
    attachments: list[str] = Field(default_factory=list)  # 用户发送的附件（沙箱路径）
    vision_attachments: list[MediaAttachment] = Field(default_factory=list)
