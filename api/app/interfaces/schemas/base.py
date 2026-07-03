#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import TypeVar, Generic, Optional

from pydantic import BaseModel

T = TypeVar("T")


class Response(BaseModel, Generic[T]):
    """基础API响应结构，继承BaseModel，并定义泛型"""
    code: int = 200  # 业务状态码，和HTTP状态码保持一致
    msg: str = "success"  # 响应消息提示
    data: Optional[T] = None  # 响应数据，无数据时为 None

    @staticmethod
    def success(data: Optional[T] = None, msg: str = "success") -> "Response[T]":
        """成功消息，传递data+msg，code固定为200"""
        return Response(code=200, msg=msg, data=data)

    @staticmethod
    def fail(code: int, msg: str, data: Optional[T] = None) -> "Response[T]":
        """失败消息提示，携带code+msg+data"""
        return Response(code=code, msg=msg, data=data)
