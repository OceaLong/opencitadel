"""Persist the selected existing contract and bind an exact, canonical intent."""

from uuid import UUID, uuid5

from app.domain.evaluation.configuration import digest
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.execution.tool_contract import (
    RecordedContract,
    ToolApprovalSubject,
    ToolContractChanged,
)


def finalize_subject(subject: ToolApprovalSubject, arguments: dict) -> ToolApprovalSubject:
    arguments_digest = digest(arguments)
    identity = digest(
        {
            "tool_call_id": subject.tool_call_id,
            "tool_name": subject.tool_name,
            "contract_digest": subject.contract_digest,
            "arguments_digest": arguments_digest,
            "scope_digest": subject.scope_digest,
            "authorization_epoch": subject.authorization_epoch,
        }
    )
    return subject.model_copy(
        update={
            "invocation_id": uuid5(subject.originating_activity_id, f"invocation:v1:{identity}"),
            "arguments_digest": arguments_digest,
            "target_digest": digest({"rule": "all_arguments_v1", "arguments": arguments}),
        }
    )


async def freeze_calls(objects, request, snapshot, calls):
    by_name = {definition.name: definition for definition in snapshot.definitions}
    for call in calls:
        definition = by_name[call["name"]]
        contract = definition.contract
        if contract is None:
            # Replay/isolated authorities retain their existing exact binding.
            continue
        if not snapshot.authority:
            raise ToolContractChanged("authorization_missing")
        ref, object_digest = await objects.put_input(
            UUID(request.aggregate_id), contract.model_dump(mode="json")
        )
        subject = ToolApprovalSubject(
            invocation_id=uuid5(request.activity_id, call["call_id"]),
            originating_activity_id=request.activity_id,
            tool_call_id=call["call_id"],
            tool_name=call["name"],
            contract_ref=ref,
            contract_object_digest=object_digest,
            contract_digest=contract.digest,
            arguments_digest=digest(call["arguments"]),
            target_digest=digest({"rule": "all_arguments_v1", "arguments": call["arguments"]}),
            scope_digest=digest(snapshot.authority["scope"]),
            authorization_epoch=snapshot.authority["authorization_epoch"],
        )
        call["subject"] = finalize_subject(subject, call["arguments"]).model_dump(mode="json")
        call["pure_clarification"] = contract.is_pure_clarification
        if definition.approval_feedback_param:
            call["approval_feedback_param"] = definition.approval_feedback_param
    return calls


async def load_contract(
    objects, subject: ToolApprovalSubject, *, call, request, bounded=False, read_limit=65536
):
    if not subject.contract_ref.startswith(f"execution/inputs/{request.aggregate_id}/"):
        raise ToolContractChanged("invocation_binding_invalid")
    load = objects.load_input_bounded if bounded else objects.load_input
    contract = RecordedContract.model_validate(
        await load(
            key=subject.contract_ref,
            expected_digest=subject.contract_object_digest,
            **({"limit": read_limit} if bounded else {}),
        )
    )
    final = finalize_subject(subject, call["arguments"])
    if (
        contract.contract_schema_version != 2
        or contract.digest != subject.contract_digest
        or contract.name != call["name"]
        or contract.name != subject.tool_name
        or call["call_id"] != subject.tool_call_id
        or final != subject
    ):
        raise ToolContractChanged("invocation_binding_invalid")
    return contract


async def verify_authority(uow, context, subject, contract):
    try:
        authority = await uow.execution_usage.invocation_authority(
            context.run.owner_scope, context.run.run_id, lock=True
        )
        if (
            digest(authority["scope"]) != subject.scope_digest
            or authority["authorization_epoch"] != subject.authorization_epoch
        ):
            raise ToolContractChanged("authorization_changed")
        for connector_id, expected in contract.connector_bindings.items():
            current = await uow.evaluation_recording.connector_binding(
                context.run.owner_scope,
                contract.pack,
                connector_id,
                lock=True,
                execution=True,
                tool_name=contract.source_name,
            )
            if current != expected:
                raise ToolContractChanged("contract_changed")
    except ReplayMismatch as error:
        raise ToolContractChanged("contract_changed") from error
    except PermissionError as error:
        raise ToolContractChanged("authorization_changed") from error
