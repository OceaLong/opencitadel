#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Datetime helpers.

数据库 datetime 列统一为 naive UTC（timestamp without time zone）；
HTTP 层收到的 ISO 字符串常带时区（如前端 toISOString 的 Z 后缀），
必须在边界处规范化，否则 asyncpg 绑定参数时会抛
"can't subtract offset-naive and offset-aware datetimes"。
"""
from datetime import datetime, timezone
from typing import Optional


def to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """把 tz-aware datetime 转为等价的 naive UTC；naive/None 原样返回。"""
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)
