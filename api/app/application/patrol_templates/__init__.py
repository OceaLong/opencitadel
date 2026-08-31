"""Versioned built-in Patrol Pack templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.domain.errors import BadRequestError
from app.domain.models.patrol import PatrolPackConfig

_TEMPLATES = {
    "kubernetes-baseline-v1": Path(__file__).with_name("kubernetes_daily_patrol.v1.yaml"),
    "compose-services-baseline-v1": Path(__file__).with_name("compose_services_patrol.v1.yaml"),
}


def load_patrol_template(
    template_id: str, overrides: dict[str, Any] | None = None
) -> PatrolPackConfig:
    path = _TEMPLATES.get(template_id)
    if path is None:
        raise BadRequestError(
            "Unknown Patrol template", error_key="apiErrors.patrol.unknownTemplate"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in (overrides or {}).items():
        data[key] = value
    namespaces = data.get("scope", {}).get("namespaces") or []
    if namespaces:
        for check in data.get("checks", []):
            args = check.get("probe", {}).get("args", {})
            if "namespace" in args:
                args["namespace"] = namespaces[0]
    return PatrolPackConfig.model_validate(data)
