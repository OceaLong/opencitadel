#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SSRF-safe URL validation for knowledge-base web ingestion."""
from typing import Optional

from app.application.errors.exceptions import BadRequestError
from app.application.services.config_provider import get_runtime_config
from app.domain.utils.outbound_url import (
    OutboundURLRejected,
    resolve_outbound_url,
)


def validate_public_url(url: str, *, allowlist: Optional[list[str]] = None) -> str:
    """Validate URL scheme/host and block private/metadata targets."""
    cfg = get_runtime_config().knowledge_base.connectors
    effective_allowlist = allowlist if allowlist is not None else (cfg.url_allowlist or [])
    denylist = cfg.url_denylist or []
    try:
        target = resolve_outbound_url(
            url,
            allowed_ports={80, 443},
            allowlist=effective_allowlist,
            denylist=denylist,
        )
    except OutboundURLRejected as exc:
        raise BadRequestError(
            str(exc),
            error_key="errors.urlNotAllowed",
        ) from exc
    return target.url
