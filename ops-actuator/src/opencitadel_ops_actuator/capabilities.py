"""Deterministic schema manifest for the fixed write MCP surface.

Hash algorithm (canonical JSON -> sha256) and manifest shape mirror
ops-collector/src/opencitadel_ops_collector/capabilities.py:34-36.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import __version__
from .contracts import (
    ActuatorEnvelope,
    GetOperationRequest,
    GetTargetRequest,
    RestartRequest,
    RollbackRequest,
    ScaleRequest,
    TargetIdentity,
)

TOOL_INPUT_MODELS = {
    "get_capabilities": None,
    "get_operation": GetOperationRequest,
    "get_target": GetTargetRequest,
    "restart_workload": RestartRequest,
    "scale_workload": ScaleRequest,
    "rollback_workload": RollbackRequest,
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
    output_schema = ActuatorEnvelope.model_json_schema()
    input_hashes = {name: canonical_hash(schema) for name, schema in input_schemas.items()}
    output_hashes = {name: canonical_hash(output_schema) for name in TOOL_INPUT_MODELS}
    output_hashes["get_target"] = canonical_hash(TargetIdentity.model_json_schema())
    core = {
        "schema_version": 2,
        "actuator_version": __version__,
        "target_ref": target_ref,
        "enabled_tools": sorted(TOOL_INPUT_MODELS),
        "input_schema_hashes": input_hashes,
        "output_schema_hashes": output_hashes,
        "effect_contract": {
            "version": 2,
            "execute": True,
            "resolve": "get_operation",
            "target_identity": "get_target",
            "cancel": False,
            "idempotency_scope": "provider_namespace/target_ref/caller_authority/key",
            "key_retention": "indefinite; no receipt or key deletion",
            "minimum_receipt_retention_days": 90,
            "same_key_different_payload": "OPERATION_CONFLICT",
            "query_semantics": "journal receipt; target UID and operation/digest proof only",
            "journal_unavailable": "fail_closed_before_patch; prior outcome may be unknown",
            "max_journal_bytes": 524288,
            "receipt_reserve_bytes": 16384,
            "patch_policy": "fixed original UID/resourceVersion; conflict resolves, never rebases",
            "rollback_literal_policy": "reject literal env values, commands, args, secret-shaped data",
            "caller_authority": "configured principal of the authenticated static bearer token",
            "digest_contract": "sha256 canonical UTF-8 JSON: action,target_ref,namespace,workload,kind,target_uid,parameters",
        },
    }
    return {**core, "overall_capability_hash": canonical_hash(core)}
