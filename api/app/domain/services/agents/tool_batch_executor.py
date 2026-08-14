#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Whole-batch tool-call preflight and governed execution."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
from hashlib import sha256
import inspect
import json
import time
from typing import Any, Awaitable, Callable, Optional, Sequence
import uuid

from app.domain.models.tool_approval import (
    ApprovalCallInput,
    ApprovalStatus,
    ToolApprovalBatch,
    hash_normalized_args,
)
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.models.tool_execution import (
    ToolExecutionAttempt,
    ToolExecutionStatus,
)
from app.domain.models.tool_result import ToolResult, normalize_tool_result
from app.domain.repositories.resource_governance_repository import (
    ResourceGovernanceRepository,
)
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.capability_policy import (
    CapabilityDeniedError,
    CapabilityPolicy,
)
from app.domain.services.tools.tool_names import normalize_tool_name
from app.domain.utils.audit_redaction import summarize_tool_result
from app.infrastructure.observability.governance_metrics import (
    observe_approval_decision_seconds,
    record_approval_batch_outcome,
    record_tool_execution,
)


ApprovalResolver = Callable[
    ["PreparedToolCall"],
    bool | Awaitable[bool],
]
AuthorizationResolver = Callable[
    ["PreparedToolCall"],
    bool | Awaitable[bool],
]
ToolInvoker = Callable[
    [BaseTool, str, dict[str, Any]],
    Awaitable[ToolResult],
]
ToolAuditRecorder = Callable[
    [str, dict[str, Any], ToolResult, float],
    Awaitable[None],
]
# (tool_name, layer, reason) -> None; wired to
# ToolAuditRecorder.record_tool_denied by BaseAgent (Phase A / Task 2).
DenialAuditRecorder = Callable[[str, str, str], Awaitable[None]]
UnitOfWorkFactory = Callable[[], IUnitOfWork]
STATEFUL_CONCURRENCY_GROUPS = frozenset({"browser", "shell"})
RETRYABLE_TRANSPORT_ERRNOS = frozenset({
    errno.ECONNABORTED,
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.EHOSTUNREACH,
    errno.ENETDOWN,
    errno.ENETUNREACH,
    errno.ETIMEDOUT,
    errno.EPIPE,
})


class UowApprovalRepository:
    """Open a short unit of work for each approval repository operation."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def save_approval_batch(self, batch: ToolApprovalBatch) -> None:
        async with self._uow_factory() as uow:
            await uow.resource_governance.save_approval_batch(batch)

    async def get_pending_approval_batch(
        self,
        session_id: str,
    ) -> Optional[ToolApprovalBatch]:
        async with self._uow_factory() as uow:
            return await uow.resource_governance.get_pending_approval_batch(
                session_id
            )

    async def get_approval_batch(
        self,
        batch_id: str,
    ) -> Optional[ToolApprovalBatch]:
        async with self._uow_factory() as uow:
            return await uow.resource_governance.get_approval_batch(batch_id)

    async def decide_approval_call(
        self,
        tool_call_id: str,
        status: ApprovalStatus,
        decided_by: str,
    ):
        async with self._uow_factory() as uow:
            return await uow.resource_governance.decide_approval_call(
                tool_call_id,
                status,
                decided_by,
            )

    async def consume_approval_batch(
        self,
        batch_id: str,
    ) -> Optional[ToolApprovalBatch]:
        async with self._uow_factory() as uow:
            return await uow.resource_governance.consume_approval_batch(
                batch_id
            )


@dataclass(frozen=True)
class PreparedToolCall:
    tool_call_id: str
    tool: BaseTool
    function_name: str
    normalized_args: dict[str, Any]
    policy: ToolExecutionPolicy
    requires_approval: bool
    ordinal: int


@dataclass(frozen=True)
class PreparedToolBatch:
    batch_id: str
    calls: tuple[PreparedToolCall, ...]
    approval_required: bool
    durable_execution_required: bool = False


@dataclass(frozen=True)
class ToolCallExecutionResult:
    tool_call_id: str
    function_name: str
    result: ToolResult
    ordinal: int


@dataclass(frozen=True)
class ToolBatchExecutionResult:
    calls: tuple[ToolCallExecutionResult, ...] = ()
    prepared_calls: tuple[PreparedToolCall, ...] = ()
    waiting: bool = False
    approval_batch: Optional[ToolApprovalBatch] = None
    rejected_reason: Optional[str] = None


class ToolBatchExecutor:
    def __init__(
        self,
        *,
        session_id: str,
        tools: Sequence[BaseTool],
        approval_repository: ResourceGovernanceRepository,
        capability_policy: Optional[CapabilityPolicy] = None,
        json_parser: Any = None,
        approval_resolver: Optional[ApprovalResolver] = None,
        authorization_resolver: Optional[AuthorizationResolver] = None,
        invoke_call: Optional[ToolInvoker] = None,
        audit_call: Optional[ToolAuditRecorder] = None,
        denial_audit_call: Optional[DenialAuditRecorder] = None,
        stateful_tool_lock: Optional[asyncio.Lock] = None,
        max_attempts: int = 3,
        timeout_seconds: float = 120,
        retry_interval: float = 1.0,
    ) -> None:
        self._session_id = session_id
        self._tools = tuple(tools)
        self._approval_repository = approval_repository
        self._capability_policy = capability_policy
        self._json_parser = json_parser
        self._approval_resolver = approval_resolver
        self._authorization_resolver = authorization_resolver
        self._invoke_call = invoke_call
        self._audit_call = audit_call
        self._denial_audit_call = denial_audit_call
        self._stateful_tool_lock = stateful_tool_lock
        self._max_attempts = max(1, max_attempts)
        self._timeout_seconds = max(0.001, timeout_seconds)
        self._retry_interval = max(0.0, retry_interval)
        self._approval_batches: dict[str, ToolApprovalBatch] = {}
        self._resumed_batch_ids: set[str] = set()

    async def preflight(
        self,
        tool_calls: Sequence[dict[str, Any]],
        *,
        approval_already_granted: bool = False,
    ) -> PreparedToolBatch:
        prepared_calls = []
        seen_call_ids: set[str] = set()
        for ordinal, raw_call in enumerate(tool_calls):
            function = raw_call.get("function")
            if not isinstance(function, dict):
                raise ValueError(f"tool call at ordinal {ordinal} has no function")
            tool_call_id = str(raw_call.get("id") or uuid.uuid4())
            if tool_call_id in seen_call_ids:
                raise ValueError(
                    f"duplicate tool_call_id in batch: {tool_call_id}"
                )
            seen_call_ids.add(tool_call_id)

            function_name = normalize_tool_name(str(function.get("name") or ""))
            if not function_name:
                raise ValueError(
                    f"tool call {tool_call_id!r} has no function name"
                )
            tool, policy = self._resolve_tool_and_policy(function_name)
            normalized_args = await self._normalize_arguments(
                tool,
                function_name,
                function.get("arguments", {}),
            )
            prepared = PreparedToolCall(
                tool_call_id=tool_call_id,
                tool=tool,
                function_name=function_name,
                normalized_args=normalized_args,
                policy=policy,
                requires_approval=False,
                ordinal=ordinal,
            )
            await self._authorize(prepared)
            requires_approval = (
                False
                if approval_already_granted
                else await self._requires_approval(prepared)
            )
            prepared_calls.append(
                PreparedToolCall(
                    tool_call_id=prepared.tool_call_id,
                    tool=prepared.tool,
                    function_name=prepared.function_name,
                    normalized_args=prepared.normalized_args,
                    policy=prepared.policy,
                    requires_approval=requires_approval,
                    ordinal=prepared.ordinal,
                )
            )

        calls = tuple(prepared_calls)
        approval_required = any(call.requires_approval for call in calls)
        durable_execution_required = any(
            call.policy.effect != ToolEffect.READ_ONLY for call in calls
        )
        batch_id = str(uuid.uuid4())
        batch = PreparedToolBatch(
            batch_id=batch_id,
            calls=calls,
            approval_required=approval_required,
            durable_execution_required=durable_execution_required,
        )
        if approval_required or durable_execution_required:
            approval_batch = ToolApprovalBatch.for_calls(
                self._session_id,
                [
                    ApprovalCallInput(
                        tool_call_id=call.tool_call_id,
                        tool_name=call.function_name,
                        normalized_args=call.normalized_args,
                        ordinal=call.ordinal,
                        policy=call.policy,
                    )
                    for call in calls
                ],
                batch_id=batch_id,
            )
            now = datetime.now(timezone.utc)
            persisted_calls = [
                persisted.model_copy(
                    update={
                        "status": ApprovalStatus.APPROVED,
                        "decided_by": "policy",
                        "decided_at": now,
                    }
                )
                if not prepared.requires_approval
                else persisted
                for prepared, persisted in zip(
                    calls,
                    approval_batch.calls,
                )
            ]
            approval_batch = approval_batch.model_copy(
                update={
                    "calls": persisted_calls,
                    "status": (
                        ApprovalStatus.PENDING
                        if any(
                            call.status == ApprovalStatus.PENDING
                            for call in persisted_calls
                        )
                        else ApprovalStatus.APPROVED
                    ),
                    "decided_at": (
                        None
                        if any(
                            call.status == ApprovalStatus.PENDING
                            for call in persisted_calls
                        )
                        else now
                    ),
                }
            )
            await self._approval_repository.save_approval_batch(approval_batch)
            self._approval_batches[batch_id] = approval_batch
        return batch

    async def invoke(self, tool_call: dict[str, Any]) -> ToolResult:
        """Preflight and execute a single governed call for programmatic users."""
        batch = await self.preflight([tool_call])
        execution = await self.execute(batch)
        if execution.calls:
            return execution.calls[0].result
        return ToolResult(
            success=False,
            message=execution.rejected_reason or "tool call is awaiting approval",
        )

    async def invoke_preapproved(self, tool_call: dict[str, Any]) -> ToolResult:
        """Execute a legacy user-approved call through normal policy retries."""
        batch = await self.preflight(
            [tool_call],
            approval_already_granted=True,
        )
        execution = await self.execute(batch)
        if execution.calls:
            return execution.calls[0].result
        return ToolResult(
            success=False,
            message=execution.rejected_reason or "tool call could not execute",
        )

    async def execute(
        self,
        batch: PreparedToolBatch,
    ) -> ToolBatchExecutionResult:
        if batch.approval_required:
            return ToolBatchExecutionResult(
                waiting=True,
                approval_batch=self._approval_batches.get(batch.batch_id),
            )
        if batch.durable_execution_required or any(
            call.policy.effect != ToolEffect.READ_ONLY for call in batch.calls
        ):
            return await self.resume(batch.batch_id, actor_id="policy")
        return await self.execute_approved(batch.calls)

    async def execute_approved(
        self,
        calls: Sequence[PreparedToolCall],
    ) -> ToolBatchExecutionResult:
        parallel_reads: list[PreparedToolCall] = []
        serialized_lanes: dict[str, list[PreparedToolCall]] = {}
        for call in sorted(calls, key=lambda item: item.ordinal):
            if (
                call.policy.effect == ToolEffect.READ_ONLY
                and call.policy.concurrency_group == "none"
            ):
                parallel_reads.append(call)
                continue
            lane = (
                call.policy.concurrency_group
                if call.policy.concurrency_group != "none"
                else "__effectful__"
            )
            serialized_lanes.setdefault(lane, []).append(call)

        tasks: list[Awaitable[list[ToolCallExecutionResult]]] = [
            self._execute_lane([call])
            for call in parallel_reads
        ]
        tasks.extend(
            self._execute_lane(lane_calls)
            for lane_calls in serialized_lanes.values()
        )
        lane_results = await asyncio.gather(*tasks) if tasks else []
        results = sorted(
            (
                result
                for lane_result in lane_results
                for result in lane_result
            ),
            key=lambda item: item.ordinal,
        )
        return ToolBatchExecutionResult(
            calls=tuple(results),
            prepared_calls=tuple(
                sorted(calls, key=lambda item: item.ordinal)
            ),
        )

    @staticmethod
    def _record_batch_terminal_outcome(
        batch: Optional[ToolApprovalBatch],
        outcome: str,
    ) -> None:
        """Record a batch's first transition into a terminal outcome.

        Call this only at the point where THIS method observed/caused the
        transition (see resume()'s call sites) — never from the in-memory
        `_resumed_batch_ids` guard or from a failed consume-claim, both of
        which just re-observe a state some earlier call already recorded.
        """
        record_approval_batch_outcome(outcome)
        if batch is not None and batch.decided_at and batch.created_at:
            observe_approval_decision_seconds(
                (batch.decided_at - batch.created_at).total_seconds()
            )

    async def resume(
        self,
        batch_id: str,
        *,
        actor_id: str,
    ) -> ToolBatchExecutionResult:
        del actor_id  # Decisions are persisted upstream by decide_approval_call (chat resume path).
        if batch_id in self._resumed_batch_ids:
            return ToolBatchExecutionResult(rejected_reason="approval_consumed")

        approval_batch = await self._approval_repository.get_approval_batch(
            batch_id
        )
        if approval_batch is None:
            return ToolBatchExecutionResult(rejected_reason="approval_not_found")
        if approval_batch.session_id != self._session_id:
            return ToolBatchExecutionResult(
                approval_batch=approval_batch,
                rejected_reason="approval_session_mismatch",
            )
        if approval_batch.status == ApprovalStatus.EXPIRED:
            self._record_batch_terminal_outcome(approval_batch, "expired")
            return ToolBatchExecutionResult(
                approval_batch=approval_batch,
                rejected_reason="approval_expired",
            )
        if approval_batch.status == ApprovalStatus.CONSUMED:
            return ToolBatchExecutionResult(
                approval_batch=approval_batch,
                rejected_reason="approval_consumed",
            )
        if approval_batch.expires_at <= datetime.now(timezone.utc):
            expired = await self._approval_repository.consume_approval_batch(
                batch_id
            )
            self._record_batch_terminal_outcome(
                expired or approval_batch, "expired"
            )
            return ToolBatchExecutionResult(
                approval_batch=expired or approval_batch,
                rejected_reason="approval_expired",
            )
        if approval_batch.status == ApprovalStatus.REJECTED or any(
            call.status == ApprovalStatus.REJECTED
            for call in approval_batch.calls
        ):
            self._record_batch_terminal_outcome(approval_batch, "rejected")
            return ToolBatchExecutionResult(
                approval_batch=approval_batch,
                rejected_reason="approval_rejected",
            )
        if approval_batch.status == ApprovalStatus.PENDING or any(
            call.status != ApprovalStatus.APPROVED
            for call in approval_batch.calls
        ):
            return ToolBatchExecutionResult(
                approval_batch=approval_batch,
                rejected_reason="approval_pending",
            )
        if approval_batch.status != ApprovalStatus.APPROVED:
            return ToolBatchExecutionResult(
                approval_batch=approval_batch,
                rejected_reason=f"approval_{approval_batch.status.value}",
            )

        try:
            prepared = await self._restore_prepared_batch(approval_batch)
        except Exception:
            return ToolBatchExecutionResult(
                approval_batch=approval_batch,
                rejected_reason="approval_validation_failed",
            )

        claimed_batch = (
            await self._approval_repository.consume_approval_batch(batch_id)
        )
        if claimed_batch is None:
            return ToolBatchExecutionResult(rejected_reason="approval_not_found")
        if claimed_batch.session_id != self._session_id:
            return ToolBatchExecutionResult(
                approval_batch=claimed_batch,
                rejected_reason="approval_session_mismatch",
            )
        if claimed_batch.status == ApprovalStatus.EXPIRED:
            self._record_batch_terminal_outcome(claimed_batch, "expired")
            return ToolBatchExecutionResult(
                approval_batch=claimed_batch,
                rejected_reason="approval_expired",
            )
        if claimed_batch.status == ApprovalStatus.REJECTED:
            self._record_batch_terminal_outcome(claimed_batch, "rejected")
            return ToolBatchExecutionResult(
                approval_batch=claimed_batch,
                rejected_reason="approval_rejected",
            )
        if claimed_batch.status == ApprovalStatus.PENDING:
            return ToolBatchExecutionResult(
                approval_batch=claimed_batch,
                rejected_reason="approval_pending",
            )
        if (
            claimed_batch.status != ApprovalStatus.CONSUMED
            or not claimed_batch.execution_claimed
        ):
            # Someone else already consumed this batch (race) — not this
            # call's own atomic transition, so it is deliberately NOT
            # recorded here (see consumed handling below and the note on
            # _record_batch_terminal_outcome).
            return ToolBatchExecutionResult(
                approval_batch=claimed_batch,
                rejected_reason="approval_consumed",
            )

        # This is the one place a batch can genuinely, atomically become
        # CONSUMED — `consume_approval_batch` only sets execution_claimed on
        # the transaction that flips APPROVED -> CONSUMED. Every other
        # "consumed" observation above is a re-read of that fact and must
        # not double-count the outcome.
        self._record_batch_terminal_outcome(claimed_batch, "consumed")
        self._resumed_batch_ids.add(batch_id)
        result = await self.execute_approved(prepared.calls)
        return ToolBatchExecutionResult(
            calls=result.calls,
            prepared_calls=prepared.calls,
            approval_batch=claimed_batch,
        )

    async def get_pending_approval_batch(self) -> Optional[ToolApprovalBatch]:
        return (
            await self._approval_repository.get_pending_approval_batch(
                self._session_id
            )
        )

    async def decide_approval_call(
        self,
        tool_call_id: str,
        status: ApprovalStatus,
        *,
        actor_id: str,
    ):
        return await self._approval_repository.decide_approval_call(
            tool_call_id,
            status,
            actor_id,
        )

    def _resolve_tool_and_policy(
        self,
        function_name: str,
    ) -> tuple[BaseTool, ToolExecutionPolicy]:
        denied_policy: Optional[ToolExecutionPolicy] = None
        for tool_pack in self._tools:
            try:
                descriptor = tool_pack.get_tool_descriptor(function_name)
            except (AttributeError, ValueError):
                continue
            if self._capability_policy is not None and not (
                self._capability_policy.allows_integration(
                    descriptor.policy,
                    tool_name=function_name,
                )
                if descriptor.tool_pack in {"mcp", "a2a"}
                else self._capability_policy.allows(
                    descriptor.policy,
                    tool_name=function_name,
                )
            ):
                denied_policy = descriptor.policy
                continue
            if not tool_pack.has_tool(function_name):
                denied_policy = descriptor.policy
                continue
            return tool_pack, descriptor.policy
        if denied_policy is not None:
            raise CapabilityDeniedError(
                f"当前会话策略禁止工具[{function_name}]",
                layer="execution",
                tool_name=function_name,
            )
        raise ValueError(f"工具[{function_name}]未找到")

    async def _normalize_arguments(
        self,
        tool_pack: BaseTool,
        function_name: str,
        raw_arguments: Any,
    ) -> dict[str, Any]:
        if isinstance(raw_arguments, str):
            if self._json_parser is None:
                parsed = json.loads(raw_arguments)
            else:
                parser = getattr(self._json_parser, "invoke", self._json_parser)
                parsed = parser(raw_arguments)
                if inspect.isawaitable(parsed):
                    parsed = await parsed
        else:
            parsed = raw_arguments
        if not isinstance(parsed, dict):
            raise TypeError(
                f"arguments for {function_name} must be an object"
            )

        descriptor = tool_pack.get_tool_descriptor(function_name)
        normalized = BaseTool._filter_parameters(descriptor.method, parsed)
        inspect.signature(descriptor.method).bind(**normalized)
        return normalized

    async def _authorize(self, call: PreparedToolCall) -> None:
        if self._authorization_resolver is None:
            return
        allowed = self._authorization_resolver(call)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if not allowed:
            raise PermissionError(
                f"tool call {call.tool_call_id!r} is not authorized"
            )

    async def _requires_approval(self, call: PreparedToolCall) -> bool:
        if call.policy.approval == ApprovalMode.ALWAYS:
            return True
        if call.policy.approval == ApprovalMode.NEVER:
            return False
        if self._approval_resolver is None:
            return True
        decision = self._approval_resolver(call)
        if inspect.isawaitable(decision):
            decision = await decision
        return bool(decision)

    async def _execute_lane(
        self,
        calls: Sequence[PreparedToolCall],
    ) -> list[ToolCallExecutionResult]:
        if self._stateful_tool_lock is not None and any(
            call.policy.concurrency_group in STATEFUL_CONCURRENCY_GROUPS
            or call.tool.name in STATEFUL_CONCURRENCY_GROUPS
            for call in calls
        ):
            async with self._stateful_tool_lock:
                return [await self._execute_call(call) for call in calls]
        return [await self._execute_call(call) for call in calls]

    async def _execute_call(
        self,
        call: PreparedToolCall,
    ) -> ToolCallExecutionResult:
        started = time.monotonic()
        args_hash = hash_normalized_args(call.normalized_args)
        key_contract_supported = self._supports_idempotency_key(call)
        idempotency_key = (
            self._stable_idempotency_key(call, args_hash)
            if key_contract_supported
            else None
        )
        invocation_args = dict(call.normalized_args)
        if idempotency_key is not None:
            invocation_args["idempotency_key"] = idempotency_key

        attempt_limit = self._attempt_limit(call.policy, key_contract_supported)
        attempts: list[ToolExecutionAttempt] = []
        result = ToolResult(success=False, message="tool call did not execute")
        denied = False
        denial_layer = "execution"
        for attempt_number in range(1, attempt_limit + 1):
            attempt_started_at = datetime.now(timezone.utc)
            attempt_started = time.monotonic()
            timed_out = False
            denied = False
            try:
                if self._invoke_call is None:
                    raw_result = await asyncio.wait_for(
                        call.tool.invoke(call.function_name, **invocation_args),
                        timeout=self._timeout_seconds,
                    )
                else:
                    raw_result = await asyncio.wait_for(
                        self._invoke_call(
                            call.tool,
                            call.function_name,
                            invocation_args,
                        ),
                        timeout=self._timeout_seconds,
                    )
                result = normalize_tool_result(raw_result)
            except asyncio.TimeoutError:
                timed_out = True
                result = ToolResult(
                    success=False,
                    message=f"tool call timed out after {self._timeout_seconds:g}s",
                    failure_kind="timeout",
                )
            except CapabilityDeniedError as exc:
                denied = True
                denial_layer = getattr(exc, "layer", None) or "execution"
                result = ToolResult(
                    success=False,
                    message=str(exc),
                    failure_kind=self._failure_kind(exc),
                )
            except Exception as exc:
                result = ToolResult(
                    success=False,
                    message=str(exc),
                    failure_kind=self._failure_kind(exc),
                )

            transient = self._is_transient_failure(result, timed_out=timed_out)
            status = self._status_for_attempt(
                call.policy,
                result,
                timed_out=timed_out,
            )
            attempts.append(
                ToolExecutionAttempt(
                    attempt_number=attempt_number,
                    started_at=attempt_started_at,
                    duration_ms=int((time.monotonic() - attempt_started) * 1000),
                    status=status,
                    result_summary=summarize_tool_result(result),
                    transient_failure=transient,
                    idempotency_key=idempotency_key,
                )
            )
            if result.success or not transient or attempt_number == attempt_limit:
                break
            await asyncio.sleep(self._retry_interval)

        terminal_status = attempts[-1].status
        result = result.model_copy(
            update={"status": terminal_status, "attempts": attempts}
        )
        if denied:
            record_tool_execution(call.function_name, "denied", None)
            if self._denial_audit_call is not None:
                await self._denial_audit_call(
                    call.function_name,
                    denial_layer,
                    result.message or "",
                )
        else:
            record_tool_execution(
                call.function_name,
                "ok" if result.success else "error",
                time.monotonic() - started,
            )
        if self._audit_call is not None:
            await self._audit_call(
                call.function_name,
                call.normalized_args,
                result,
                started,
            )
        return ToolCallExecutionResult(
            tool_call_id=call.tool_call_id,
            function_name=call.function_name,
            result=result,
            ordinal=call.ordinal,
        )

    def _attempt_limit(
        self,
        policy: ToolExecutionPolicy,
        key_contract_supported: bool,
    ) -> int:
        if policy.idempotency in {
            ToolIdempotency.NON_IDEMPOTENT,
            ToolIdempotency.UNKNOWN,
        }:
            return 1
        if (
            policy.idempotency == ToolIdempotency.IDEMPOTENT_WITH_KEY
            and not key_contract_supported
        ):
            return 1
        return self._max_attempts

    @staticmethod
    def _supports_idempotency_key(call: PreparedToolCall) -> bool:
        if call.policy.idempotency != ToolIdempotency.IDEMPOTENT_WITH_KEY:
            return False
        descriptor = call.tool.get_tool_descriptor(call.function_name)
        properties = (
            descriptor.schema.get("function", {})
            .get("parameters", {})
            .get("properties", {})
        )
        if "idempotency_key" not in properties:
            return False
        signature = inspect.signature(descriptor.method)
        return "idempotency_key" in signature.parameters

    def _stable_idempotency_key(
        self,
        call: PreparedToolCall,
        args_hash: str,
    ) -> Optional[str]:
        if call.policy.idempotency != ToolIdempotency.IDEMPOTENT_WITH_KEY:
            return None
        payload = f"{self._session_id}{call.tool_call_id}{args_hash}"
        return sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _failure_kind(exc: Exception) -> str:
        if isinstance(exc, PermissionError):
            return "authorization"
        if isinstance(exc, ConnectionError):
            return "transport"
        if (
            isinstance(exc, OSError)
            and exc.errno in RETRYABLE_TRANSPORT_ERRNOS
        ):
            return "transport"
        return exc.__class__.__name__.lower()

    @staticmethod
    def _is_transient_failure(
        result: ToolResult,
        *,
        timed_out: bool,
    ) -> bool:
        if result.success:
            return False
        if timed_out or result.failure_kind == "timeout":
            return True
        return result.failure_kind == "transport"

    @staticmethod
    def _status_for_attempt(
        policy: ToolExecutionPolicy,
        result: ToolResult,
        *,
        timed_out: bool,
    ) -> ToolExecutionStatus:
        if result.success:
            return ToolExecutionStatus.SUCCESS
        if (
            (timed_out or result.failure_kind == "timeout")
            and policy.effect != ToolEffect.READ_ONLY
        ):
            return ToolExecutionStatus.OUTCOME_UNKNOWN
        return ToolExecutionStatus.FAILED

    async def _restore_prepared_batch(
        self,
        approval_batch: ToolApprovalBatch,
    ) -> PreparedToolBatch:
        if approval_batch.session_id != self._session_id:
            raise PermissionError(
                f"approval batch {approval_batch.id!r} belongs to another session"
            )
        restored = []
        for persisted in sorted(
            approval_batch.calls,
            key=lambda item: item.ordinal,
        ):
            if (
                hash_normalized_args(persisted.normalized_args)
                != persisted.args_hash
            ):
                raise ValueError(
                    f"approved arguments changed for {persisted.tool_call_id}"
                )
            tool_pack, policy = self._resolve_tool_and_policy(
                persisted.tool_name
            )
            if (
                policy.capability != persisted.capability
                or policy.effect != persisted.effect
                or policy.idempotency != persisted.idempotency
                or policy.approval != persisted.approval
                or policy.concurrency_group != persisted.concurrency_group
            ):
                raise PermissionError(
                    f"tool policy changed for {persisted.tool_call_id}"
                )
            normalized_args = await self._normalize_arguments(
                tool_pack,
                persisted.tool_name,
                persisted.normalized_args,
            )
            if (
                normalized_args != persisted.normalized_args
                or hash_normalized_args(normalized_args) != persisted.args_hash
            ):
                raise ValueError(
                    f"approved arguments no longer normalize identically for "
                    f"{persisted.tool_call_id}"
                )
            call = PreparedToolCall(
                tool_call_id=persisted.tool_call_id,
                tool=tool_pack,
                function_name=persisted.tool_name,
                normalized_args=normalized_args,
                policy=policy,
                requires_approval=True,
                ordinal=persisted.ordinal,
            )
            await self._authorize(call)
            restored.append(call)
        return PreparedToolBatch(
            batch_id=approval_batch.id,
            calls=tuple(restored),
            approval_required=False,
            durable_execution_required=any(
                call.policy.effect != ToolEffect.READ_ONLY
                for call in restored
            ),
        )
