"""Deterministic schema manifest for the fixed MCP surface."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import __version__
from .contracts import (
    CollectorEnvelope,
    PodLogsRequest,
    RecentEventsRequest,
    RegisteredBackupRequest,
    RegisteredDependencyRequest,
    RegisteredProbeRequest,
    RegisteredQueryRequest,
    WorkloadSummaryRequest,
)

TOOL_INPUT_MODELS = {
    "get_capabilities": None,
    "k8s_workload_summary": WorkloadSummaryRequest,
    "k8s_recent_events": RecentEventsRequest,
    "k8s_pod_logs": PodLogsRequest,
    "prom_query": RegisteredQueryRequest,
    "http_probe": RegisteredProbeRequest,
    "certificate_status": RegisteredProbeRequest,
    "backup_status": RegisteredBackupRequest,
    "dependency_status": RegisteredDependencyRequest,
}


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def capability_manifest(target_ref: str) -> dict[str, Any]:
    input_schemas = {
        name: (model.model_json_schema() if model else {"type": "object", "properties": {}})
        for name, model in TOOL_INPUT_MODELS.items()
    }
    output_schema = CollectorEnvelope.model_json_schema()
    input_hashes = {name: canonical_hash(schema) for name, schema in input_schemas.items()}
    output_hashes = {name: canonical_hash(output_schema) for name in TOOL_INPUT_MODELS}
    core = {
        "schema_version": 1,
        "collector_version": __version__,
        "target_ref": target_ref,
        "enabled_tools": sorted(TOOL_INPUT_MODELS),
        "input_schema_hashes": input_hashes,
        "output_schema_hashes": output_hashes,
    }
    return {**core, "overall_capability_hash": canonical_hash(core)}
