#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional


class AppException(RuntimeError):
    """基础应用异常类，继承RuntimeError"""

    def __init__(
            self,
            code: int = 400,  # 自定义业务错误码
            status_code: int = 400,
            msg: str = "应用发生错误请稍后尝试",
            data: Any = None,
            error_key: Optional[str] = "errors.appError",
            error_params: Optional[Dict[str, str]] = None,
    ):
        """构造函数，完成错误数据初始化"""
        self.code = code
        self.status_code = status_code
        self.msg = msg
        self.data = data
        self.error_key = error_key
        self.error_params = error_params
        super().__init__(msg)

    def __str__(self) -> str:
        return self.msg


class BadRequestError(AppException):
    """客户端请求错误"""

    def __init__(
            self,
            msg: str = "客户端请求错误，请检查后重试",
            error_key: Optional[str] = "errors.badRequest",
            error_params: Optional[Dict[str, str]] = None,
    ):
        super().__init__(
            status_code=400,
            code=400,
            msg=msg,
            error_key=error_key,
            error_params=error_params,
        )


class UnauthorizedError(AppException):
    """身份认证失败"""

    def __init__(
            self,
            msg: str = "未登录或登录已过期，请重新登录",
            error_key: Optional[str] = "errors.unauthorized",
            error_params: Optional[Dict[str, str]] = None,
    ):
        super().__init__(
            status_code=401,
            code=401,
            msg=msg,
            error_key=error_key,
            error_params=error_params,
        )


class ForbiddenError(AppException):
    """权限不足"""

    def __init__(
            self,
            msg: str = "无权执行该操作",
            error_key: Optional[str] = "errors.forbidden",
            error_params: Optional[Dict[str, str]] = None,
    ):
        super().__init__(
            status_code=403,
            code=403,
            msg=msg,
            error_key=error_key,
            error_params=error_params,
        )


class ConflictError(AppException):
    """资源冲突（如并发任务互斥）"""

    def __init__(
            self,
            msg: str = "操作冲突，请稍后重试",
            error_key: Optional[str] = "errors.conflict",
            error_params: Optional[Dict[str, str]] = None,
    ):
        super().__init__(
            status_code=409,
            code=409,
            msg=msg,
            error_key=error_key,
            error_params=error_params,
        )


class NotFoundError(AppException):
    """资源未找到错误"""

    def __init__(
            self,
            msg: str = "资源未找到，请核实后重试",
            error_key: Optional[str] = "errors.notFound",
            error_params: Optional[Dict[str, str]] = None,
    ):
        super().__init__(
            status_code=404,
            code=404,
            msg=msg,
            error_key=error_key,
            error_params=error_params,
        )


class ValidationError(AppException):
    """数据校验错误"""

    def __init__(
            self,
            msg: str = "请求参数数据校验错误，请核实后重试",
            error_key: Optional[str] = "errors.badRequest",
            error_params: Optional[Dict[str, str]] = None,
    ):
        super().__init__(
            status_code=422,
            code=422,
            msg=msg,
            error_key=error_key,
            error_params=error_params,
        )


class TooManyRequestsError(AppException):
    """请求过多错误（触发限流）"""

    def __init__(
            self,
            msg: str = "请求过多，触发限流，请稍后重试",
            error_key: Optional[str] = "errors.rateLimit",
            error_params: Optional[Dict[str, str]] = None,
    ):
        super().__init__(
            status_code=429,
            code=429,
            msg=msg,
            error_key=error_key,
            error_params=error_params,
        )


class ServerRequestsError(AppException):
    """服务器异常错误"""

    def __init__(
            self,
            msg: str = "服务器出现异常请稍后重试",
            error_key: Optional[str] = "errors.serverError",
            error_params: Optional[Dict[str, str]] = None,
    ):
        super().__init__(
            status_code=500,
            code=500,
            msg=msg,
            error_key=error_key,
            error_params=error_params,
        )
