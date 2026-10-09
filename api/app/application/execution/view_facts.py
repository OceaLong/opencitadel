"""Allowlisted source facts shared by committed live projection and playback."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import TypeAdapter

from app.application.dto.execution_view import (
    ArtifactReference,
    CitationReference,
    Completeness,
    ConfigurationSummary,
    ContentReference,
    RunView,
    SourceReference,
)
from app.application.execution import activity_types
from app.domain.execution.events import StoredEvent, _reject_public_secrets
from app.domain.utils.audit_redaction import scrub_secret_patterns


def attempt_key(activity_id: str, generation: int, claim_generation: int) -> str:
    if (
        not activity_id
        or type(generation) is not int
        or generation < 0
        or type(claim_generation) is not int
        or claim_generation < 1
    ):
        raise ValueError("invalid attempt identity")
    return str(
        uuid5(
            NAMESPACE_URL, f"opencitadel:view-attempt:{activity_id}:{generation}:{claim_generation}"
        )
    )


def request_key(activity_id: str) -> str:
    return f"activity:{activity_id}:unknown"


def safe_text(value: object, limit: int = 1024) -> str | None:
    if not isinstance(value, str):
        return None
    # Public display text is never an object reference or presigned capability.
    value = re.sub(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+", "[reference omitted]", value)
    value = re.sub(
        r"-----BEGIN .*?PRIVATE KEY-----.*?(?:-----END .*?PRIVATE KEY-----|$)",
        "[redacted]",
        value,
        flags=re.DOTALL,
    )
    return scrub_secret_patterns(value)[:limit]


@dataclass(frozen=True)
class ProjectionFact:
    formal_position: int
    progress_position: int
    observed_at: datetime | None
    kind: Literal["run", "step", "approval", "artifact", "message"]
    entity_id: str
    patch: dict
    source_kind: Literal["formal", "progress"]

    def playback_patch(self) -> dict:
        """Explicit entity adapter; ordering comes from the persisted journal cut."""
        return {
            "kind": self.kind,
            "id": self.entity_id,
            "patch": sanitize_patch(self.kind, self.patch),
        }


_FIELDS = {
    "run": {
        "status",
        "wait_reason",
        "family",
        "admitted_at",
        "terminal_at",
        "source",
        "purpose",
        "configuration",
        "usage",
        "execution_mode",
        "completeness",
    },
    "step": {
        "activity_id",
        "attempt_id",
        "logical_step_id",
        "invocation_id",
        "parent_step_id",
        "relationship",
        "status",
        "kind",
        "tool_name",
        "started_at",
        "ended_at",
        "duration_ms",
        "business_outcome",
        "end_reason",
        "removed",
        "replacement_step_id",
        "completeness",
        "progress",
        "phase",
        "progress_status",
        "public_summary",
        "artifact_refs",
        "citation_refs",
        "input_ref",
        "output_ref",
    },
    "approval": {"approval_id", "subject_activity_id", "approval_kind", "decision", "status"},
    "message": {"role", "public_summary", "step_id", "progress", "phase", "progress_status"},
    "artifact": {"artifact_id", "version", "availability"},
}


def sanitize_patch(kind: str, patch: dict) -> dict:
    if kind not in _FIELDS or set(patch) - _FIELDS[kind]:
        raise ValueError("unapproved projection fields")
    _reject_public_secrets(patch)
    result = dict(patch)
    for key, value in patch.items():
        if key == "source" and value is not None:
            result[key] = {
                k: safe_text(v) if isinstance(v, str) else v
                for k, v in SourceReference.model_validate(value).model_dump(mode="json").items()
            }
        elif key == "configuration" and value is not None:
            result[key] = {
                k: safe_text(v) if isinstance(v, str) else v
                for k, v in ConfigurationSummary.model_validate(value)
                .model_dump(mode="json")
                .items()
            }
        elif key == "usage":
            result[key] = TypeAdapter(RunView.model_fields["usage"].annotation).dump_python(
                TypeAdapter(RunView.model_fields["usage"].annotation).validate_python(value),
                mode="json",
            )
        elif key == "execution_mode":
            result[key] = TypeAdapter(
                RunView.model_fields["execution_mode"].annotation
            ).validate_python(value)
        elif key == "completeness":
            result[key] = Completeness.model_validate(value).model_dump(mode="json")
        elif key in ("input_ref", "output_ref") and value is not None:
            result[key] = ContentReference.model_validate(value).model_dump(mode="json")
        elif key == "citation_refs" and value is not None:
            result[key] = [
                CitationReference.model_validate(ref).model_dump(mode="json") for ref in value
            ]
        elif key == "artifact_refs" and value is not None:
            result[key] = [
                ArtifactReference.model_validate(ref).model_dump(mode="json") for ref in value
            ]
        elif isinstance(value, (dict, list)):
            raise ValueError("unapproved nested projection data")
        elif isinstance(value, str):
            result[key] = safe_text(value)
    return result


def to_view_fact(event: StoredEvent) -> ProjectionFact | None:
    p, name = event.public_payload, event.event_type
    if event.stream_type != "run":
        return None
    kind, entity, patch = "run", event.stream_id, {}
    if name.startswith("Activity"):
        statuses = {
            "ActivityRequested": "queued",
            "ActivityCallStarted": "running",
            "ActivityCompleted": "completed",
            "ActivityFailed": "failed",
            "ActivityOutcomeUnknown": "unknown",
            "ActivityCancelled": "cancelled",
        }
        if name not in statuses:
            return None
        kind = "step"
        activity = str(UUID(str(p["activity_id"])))
        generation, claim = p.get("generation"), p.get("claim_generation")
        attempt = (
            attempt_key(activity, generation, claim)
            if claim is not None and generation is not None
            else None
        )
        entity = attempt or request_key(activity)
        patch = {
            "activity_id": activity,
            "attempt_id": attempt,
            "logical_step_id": f"activity:{activity}",
            "invocation_id": str(UUID(str(p["invocation_id"]))) if p.get("invocation_id") else None,
            "parent_step_id": None,
            "relationship": "unknown",
            "status": statuses[name],
        }
        data = p.get("public_data") or {}
        name_value = safe_text(data.get("tool_name", data.get("name")), 255)
        if name_value:
            patch["tool_name"] = name_value
        if name == "ActivityRequested":
            patch.update(
                kind={
                    activity_types.MODEL_CALL: "model",
                    activity_types.TOOL_CALL: "tool",
                    "model": "model",
                    "tool": "tool",
                }.get(p.get("activity_type"), "activity"),
                started_at=None,
            )
        elif name == "ActivityCallStarted":
            patch["started_at"] = event.occurred_at.isoformat()
        else:
            patch["ended_at"] = event.occurred_at.isoformat()
            outcome = data.get("success")
            patch["business_outcome"] = (
                ("success" if outcome else "failure")
                if isinstance(outcome, bool)
                else (
                    {"completed": "success", "failed": "failure"}.get(data.get("status"))
                    if data.get("kind") == "tool"
                    else None
                )
            )
            if p.get("failure_code"):
                patch["end_reason"] = safe_text(p["failure_code"], 128)
    elif name.startswith("Approval"):
        kind, entity = "approval", str(UUID(str(p["approval_id"])))
        patch = {
            "approval_id": entity,
            "status": {
                "ApprovalRequested": "waiting",
                "ApprovalDecided": "decided",
                "ApprovalExpired": "expired",
            }[name],
        }
        for key in ("subject_activity_id", "approval_kind", "decision"):
            if key in p:
                patch[key] = safe_text(p[key], 128)
    elif name.startswith("Run"):
        statuses = {
            "RunCreated": "queued",
            "RunStarted": "running",
            "RunWaiting": "waiting",
            "RunResumed": "running",
            "RunCompleted": "completed",
            "RunFailed": "failed",
            "RunCancelled": "cancelled",
            "RunAttemptFailed": "waiting",
            "RunRetried": "queued",
        }
        if name not in statuses:
            return None
        patch = {
            "status": statuses[name],
            "wait_reason": safe_text(p.get("reason"), 128)
            if name == "RunWaiting"
            else "retry"
            if name == "RunAttemptFailed"
            else None,
        }
        if name == "RunCreated":
            patch.update(family=p["family"], admitted_at=event.occurred_at.isoformat())
            if p.get("source_entity_type") and p.get("source_entity_id"):
                patch["source"] = {
                    "entity_type": safe_text(p["source_entity_type"], 64),
                    "entity_id": safe_text(p["source_entity_id"], 255),
                }
                # Evaluation admission pins the suite mode in its formal source
                # type. Keep that immutable mode in live and fixed-cut playback.
                mode = {
                    "evaluation_recorded_case": "recorded",
                    "evaluation_isolated_case": "isolated",
                }.get(p["source_entity_type"])
                if mode is not None:
                    patch["execution_mode"] = mode
        if name in ("RunCompleted", "RunFailed", "RunCancelled"):
            patch["terminal_at"] = event.occurred_at.isoformat()
    else:
        return None
    return ProjectionFact(event.position, 0, None, kind, entity, patch, "formal")


def to_view_facts(event: StoredEvent) -> tuple[ProjectionFact, ...]:
    fact = to_view_fact(event)
    facts = [fact] if fact else []
    data = event.public_payload.get("public_data") or {}
    if event.event_type == "ActivityCompleted" and data.get("kind") == "message":
        facts.append(
            ProjectionFact(
                event.position,
                0,
                None,
                "message",
                str(event.event_id),
                {
                    "role": data.get("role")
                    if data.get("role") in ("assistant", "user", "system")
                    else "assistant",
                    "public_summary": safe_text(data.get("message")),
                    "step_id": fact.entity_id,
                },
                "formal",
            )
        )
    return tuple(facts)
