#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""视频抽帧服务（ffmpeg 关键帧采样 -> vision 多图）。"""
import asyncio
import base64
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from app.domain.models.message import MediaAttachment

logger = logging.getLogger(__name__)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def extract_video_frames(
        video_bytes: bytes,
        *,
        max_frames: int = 8,
        mime_type: str = "video/mp4",
) -> List[MediaAttachment]:
    """从视频字节抽取关键帧，返回 MediaAttachment 列表。"""
    if not _ffmpeg_available():
        logger.warning("ffmpeg 不可用，跳过视频抽帧")
        return []

    def _extract() -> List[MediaAttachment]:
        attachments: List[MediaAttachment] = []
        suffix = ".mp4" if "mp4" in mime_type else ".webm"
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / f"input{suffix}"
            video_path.write_bytes(video_bytes)
            out_pattern = str(Path(tmpdir) / "frame_%03d.jpg")
            cmd = [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vf", f"select=eq(pict_type\\,I),scale=640:-1",
                "-vsync", "vfr",
                "-frames:v", str(max_frames),
                out_pattern,
            ]
            try:
                subprocess.run(cmd, capture_output=True, check=True, timeout=60)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                logger.warning("ffmpeg 抽帧失败: %s", exc)
                return []

            for idx, frame_path in enumerate(sorted(Path(tmpdir).glob("frame_*.jpg"))):
                frame_bytes = frame_path.read_bytes()
                attachments.append(MediaAttachment(
                    mime_type="image/jpeg",
                    data_base64=base64.b64encode(frame_bytes).decode("ascii"),
                    media_type="video_frame",
                    frame_index=idx,
                ))
        return attachments

    return await asyncio.to_thread(_extract)


async def extract_frames_from_file_path(
        filepath: str,
        sandbox,
        *,
        max_frames: int = 8,
) -> List[MediaAttachment]:
    """从沙箱视频文件路径抽帧。"""
    try:
        file_data = await sandbox.download_file(filepath)
        video_bytes = file_data.read()
    except Exception as exc:
        logger.warning("读取视频文件失败 path=%s: %s", filepath, exc)
        return []
    mime = "video/mp4" if filepath.lower().endswith(".mp4") else "video/webm"
    return await extract_video_frames(video_bytes, max_frames=max_frames, mime_type=mime)
