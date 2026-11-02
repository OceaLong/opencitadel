"""Provider-shaped test responses: identities originate in trusted calls, never model output."""

import hashlib
import json
from datetime import UTC, datetime, timedelta


def capabilities(revision="capability-v1"):
    return {
        "schema_version": 2,
        "target_ref": "opencitadel-local",
        "overall_capability_hash": revision,
        "enabled_tools": [
            "get_target",
            "get_operation",
            "restart_workload",
            "scale_workload",
            "rollback_workload",
        ],
        "effect_contract": {
            "version": 2,
            "execute": True,
            "resolve": "get_operation",
            "target_identity": "get_target",
        },
    }


async def target_identity(_server, *, namespace, workload, kind, **kwargs):
    return {
        "schema_version": 2,
        "target_ref": "opencitadel-local",
        "namespace": namespace,
        "workload": workload,
        "kind": kind,
        "uid": "uid-api-1",
    }


def operation_envelope(remediation, state="applied"):
    target = {
        "cluster": remediation.actuator_target_ref,
        "namespace": remediation.target_namespace,
        "workload": remediation.target_workload,
        "kind": remediation.target_kind.lower(),
        "uid": remediation.actuator_target_uid,
    }
    receipt = {
        "schema_version": 1,
        "operation_id": remediation.actuator_operation_id,
        "payload_digest": remediation.actuator_payload_digest,
        "key_digest": hashlib.sha256(remediation.idempotency_key.encode()).hexdigest(),
        "target": target,
        "action": remediation.action.value,
        "state": state,
        "revision": 1,
        "caller_authority": "opencitadel-platform",
        "provider_namespace": "opencitadel-ops-actuator",
        "before": {"replicas": 1},
        "after": {"replicas": 2} if state == "applied" else None,
        "error_code": None if state == "applied" else "TARGET_UID_MISMATCH",
    }
    return {
        **{
            key: receipt[key]
            for key in ("operation_id", "payload_digest", "key_digest", "target", "action", "state")
        },
        "schema_version": 2,
        "target_ref": remediation.actuator_target_ref,
        "receipt_revision": 1,
        "receipt": receipt,
        "action_outcome": "applied" if state == "applied" else "failed",
        "evidence": [
            {
                "ref": f"actuator://operations/{remediation.actuator_operation_id}/receipts/1",
                "target_ref": remediation.actuator_target_ref,
                "sha256": hashlib.sha256(
                    json.dumps(
                        receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "expires_at": (datetime.now(UTC) + timedelta(days=90)).isoformat(),
            }
        ],
    }
