"""SSRF-safe URL validation for knowledge-base web ingestion."""

from app.domain.errors import BadRequestError
from app.domain.runtime_policy import SourceAccessPolicy
from app.domain.utils.outbound_url import (
    OutboundURLRejected,
    resolve_outbound_url,
)


def validate_public_url(url: str, *, policy: SourceAccessPolicy) -> str:
    """Validate URL scheme/host and block private/metadata targets."""
    try:
        target = resolve_outbound_url(
            url,
            allowed_ports={80, 443},
            allowlist=list(policy.url_allowlist),
            denylist=list(policy.url_denylist),
        )
    except OutboundURLRejected as exc:
        raise BadRequestError(
            str(exc),
            error_key="errors.urlNotAllowed",
        ) from exc
    return target.url
