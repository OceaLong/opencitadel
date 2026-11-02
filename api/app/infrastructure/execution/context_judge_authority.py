"""Restricted judge materials enter context only through their durable intent."""

import json

from app.application.execution import activity_types
from app.application.execution.decisions.base import activity_identity
from app.domain.evaluation.judge_protocol import SYSTEM_PROMPT
from app.domain.execution.context_models import (
    ContextMessage,
    ContextSource,
    ContextSourceRef,
    context_digest,
)


async def judge_context_sources(work, state, *, scope, scope_key, epoch, request=None):
    if state.source_entity_type != "evaluation_judge":
        raise ValueError("CONTEXT_JUDGE_AUTHORITY_INVALID")
    model_request = None
    if request is not None:
        ordinal = request.input_payload.get("round")
        cycle = request.input_payload.get("cycle")
        if (
            type(ordinal) is not int
            or not 0 <= ordinal <= 2
            or type(cycle) is not int
            or cycle != 0
            or request.activity_id != activity_identity(state, f"context:prepare:{ordinal}:0")
            or request.input_payload.get("allow_tools") is not False
        ):
            raise ValueError("CONTEXT_JUDGE_REQUEST_INVALID")
        # Validate the same restricted repair round before its model Activity is
        # scheduled. No new authorization or general-purpose tool scope exists.
        model_request = request.model_copy(
            update={
                "activity_id": activity_identity(state, f"model:{ordinal}"),
                "activity_type": activity_types.MODEL_CALL,
                "input_payload": {"round": ordinal},
            }
        )
    intent = await work.evaluation_judge.authorize_run(
        scope, state.run_id, state=state, request=model_request
    )
    values = []
    for prefix, kind, content in (
        ("judge-platform", "platform", SYSTEM_PROMPT),
        ("judge", "user", json.dumps(intent["materials"], ensure_ascii=False)),
    ):
        message = ContextMessage(role="system" if kind == "platform" else "user", content=content)
        digest = context_digest([message.model_dump(mode="json")])
        values.append(
            ContextSource(
                ref=ContextSourceRef(
                    source_id=f"{prefix}:{state.run_id}",
                    scope_key=scope_key,
                    run_id=state.run_id,
                    kind=kind,
                    digest=digest,
                    formal_position=0,
                    authorization_epoch=epoch,
                ),
                messages=(message,),
                messages_digest=digest,
                protected=True,
            )
        )
    return tuple(values)
