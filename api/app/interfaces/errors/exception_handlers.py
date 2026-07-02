#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.application.errors.exceptions import AppException
from app.infrastructure.observability.logging_context import get_request_id
from app.interfaces.schemas import Response

logger = logging.getLogger(__name__)


def _request_context(req: Request) -> str:
    request_id = getattr(req.state, "request_id", None) or get_request_id() or "-"
    return f"request_id={request_id} method={req.method} path={req.url.path}"


def register_exception_handlers(app: FastAPI) -> None:
    """处理 OpenCitadel 项目中所有的异常并进行统一处理，涵盖：自定义业务状态异常、HTTP异常、通用异常"""

    @app.exception_handler(AppException)
    async def app_exception_handler(req: Request, e: AppException) -> JSONResponse:
        """处理 OpenCitadel 业务异常，将所有状态统一响应结构"""
        logger.error("AppException: %s (%s)", e.msg, _request_context(req))
        return JSONResponse(
            status_code=e.status_code,
            content=Response(
                code=e.status_code,
                msg=e.msg,
                data={}
            ).model_dump(),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(req: Request, e: HTTPException) -> JSONResponse:
        """处理FastAPI抛出的http异常，将所有状态统一响应结构"""
        logger.error("HTTPException: %s (%s)", e.detail, _request_context(req))
        return JSONResponse(
            status_code=e.status_code,
            content=Response(
                code=e.status_code,
                msg=e.detail,
                data={}
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def exception_handler(req: Request, e: Exception) -> JSONResponse:
        """处理 OpenCitadel 中抛出的未定义的任意异常，将状态码统一设置为500"""
        logger.exception("未捕获异常: %s (%s)", e, _request_context(req))
        return JSONResponse(
            status_code=500,
            content=Response(
                code=500,
                msg="服务器出现异常请稍后重试",
                data={},
            ).model_dump()
        )
