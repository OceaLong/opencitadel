import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.models.file import File
from .base import Base


class FileModel(Base):
    """文件数据ORM模型"""

    __tablename__ = "files"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_files_id"),
        # RLS predicate shape; leading team_id also serves the teams FK scan.
        Index("ix_files_team_created", "team_id", "created_at"),
        # RLS personal scope (team_id IS NULL AND owner_user_id = :user).
        Index(
            "ix_files_owner_created",
            "owner_user_id",
            "created_at",
            postgresql_where=text("team_id IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )  # 文件id
    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("''::character varying"),
    )  # 文件名字
    filepath: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("''::character varying"),
    )  # 文件路径
    key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("''::character varying"),
    )  # 对象存储中的文件路径
    extension: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("''::character varying"),
    )  # 文件扩展名
    mime_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("''::character varying"),
    )  # 文件mime-type类型
    size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )  # 文件大小
    owner_user_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,  # users FK integrity scan (partial owner index only covers team_id IS NULL rows)
    )
    team_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )  # indexed via ix_files_team_created composite
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )  # 更新时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )  # 创建时间

    @classmethod
    def from_domain(cls, file: File) -> "FileModel":
        """从领域模型创建ORM模型"""
        return cls(**file.model_dump(mode="json"))

    def to_domain(self) -> File:
        """将ORM模型转换为领域模型"""
        return File.model_validate(self, from_attributes=True)

    def update_from_domain(self, file: File) -> None:
        """从领域模型更新数据"""
        file_data = file.model_dump(mode="json")
        for field, value in file_data.items():
            setattr(self, field, value)
