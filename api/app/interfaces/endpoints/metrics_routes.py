#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prometheus metrics endpoint.

Fail-closed (spec §A4): metrics_token unset -> 404 (feature disabled,
endpoint hidden from unauthenticated probing); token set but missing/wrong
`Authorization: Bearer <token>` -> 401; correct token -> 200. Stays on the
public router (Prometheus scrapers carry no session) but is no longer
rate-limit-exempt or unauthenticated.
"""
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from core.config import Settings, get_settings

router = APIRouter(tags=["监控"])


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token


@router.get("/metrics")
async def prometheus_metrics(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Response:
    expected_token = settings.metrics_token
    if not expected_token:
        raise HTTPException(status_code=404, detail="Not Found")

    provided_token = _extract_bearer_token(authorization)
    if not hmac.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
    except ImportError:
        return Response(content="# prometheus_client not installed\n", media_type="text/plain")
