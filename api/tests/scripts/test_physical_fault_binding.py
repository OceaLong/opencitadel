from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from scripts.acceptance import physical_fault_authority as authority
from scripts.acceptance.physical_fault_authority import verify_request
from scripts.acceptance.physical_faults import FaultError, digest


@pytest.mark.asyncio
@pytest.mark.parametrize("removed_member", [False, True])
async def test_current_preserves_complete_personal_principal_membership(removed_member):
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal
    from app.domain.models.team import TeamRole
    from app.domain.models.user import GlobalRole

    user = NS(id="owner", is_active=True, global_role=GlobalRole.ADMIN, token_version=7)
    members = {
        "first": NS(role=TeamRole.OWNER),
        "second": None if removed_member else NS(role=TeamRole.MEMBER),
    }
    team = NS(
        list_for_user=AsyncMock(return_value=[NS(id=identity) for identity in members]),
        get_member=AsyncMock(side_effect=lambda identity, _user: members[identity]),
    )

    @asynccontextmanager
    async def factory(authorization):
        assert authorization == AuthorizationContext.system("execution-kernel")
        yield NS(user=NS(get_by_id=AsyncMock(return_value=user)), team=team)

    principal, scope, authorization = await authority.current(factory, "owner")
    expected = Principal(
        user_id="owner",
        global_role=GlobalRole.ADMIN,
        token_version=7,
        team_roles={
            "first": TeamRole.OWNER,
            **({} if removed_member else {"second": TeamRole.MEMBER}),
        },
    )
    assert principal == expected
    assert scope == OwnerScope.personal("owner")
    assert authorization.principal == expected
    team.list_for_user.assert_awaited_once_with("owner")
    assert team.get_member.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [None, NS(is_active=False)])
async def test_current_rejects_missing_or_inactive_operator_before_membership_read(user):
    team = NS(list_for_user=AsyncMock())

    @asynccontextmanager
    async def factory(_authorization):
        yield NS(user=NS(get_by_id=AsyncMock(return_value=user)), team=team)

    with pytest.raises(FaultError, match="operator no longer active"):
        await authority.current(factory, "owner")
    team.list_for_user.assert_not_awaited()


def test_actual_request_must_match_armed_model_decision_and_claim():
    run, activity = uuid4(), uuid4()
    call = {"name": "shell_execute", "arguments": {"command": "owned"}, "call_id": "call"}
    arm = {
        "execution_run_id": str(run),
        "activity_id": str(activity),
        "generation": 0,
        "owner_user_id": "owner",
        "token_version": 3,
        "call_digest": digest(call),
        "catalog_fingerprint": "catalog",
    }
    context = NS(
        run=NS(run_id=run),
        activity_id=activity,
        generation=0,
        claim_generation=2,
        owner_user_id="owner",
        team_id=None,
    )
    request = NS(
        activity_id=activity,
        generation=0,
        input_payload={"tool_call": call, "catalog_fingerprint": "catalog"},
    )
    verify_request(arm, request, context)
    request.input_payload = {**request.input_payload, "catalog_fingerprint": "drift"}
    with pytest.raises(FaultError):
        verify_request(arm, request, context)
    request.input_payload = {
        "tool_call": {**call, "arguments": {}},
        "catalog_fingerprint": "catalog",
    }
    with pytest.raises(FaultError):
        verify_request(arm, request, context)
    request.activity_id = uuid4()
    with pytest.raises(FaultError):
        verify_request(arm, request, context)


@pytest.mark.asyncio
async def test_started_claim_waits_for_formal_projection(monkeypatch):
    from app.domain.execution.run import RunState

    run, activity, approval_id = uuid4(), uuid4(), uuid4()
    call = {"name": "shell_execute", "arguments": {"command": "owned"}, "call_id": "call"}
    arm = {
        "execution_run_id": str(run),
        "activity_id": str(activity),
        "approval_id": str(approval_id),
        "generation": 0,
        "owner_user_id": "owner",
        "token_version": 3,
        "call_digest": digest(call),
        "catalog_fingerprint": "catalog",
    }
    context = NS(
        run=NS(run_id=run),
        activity_id=activity,
        generation=0,
        claim_generation=1,
        owner_user_id="owner",
        team_id=None,
    )
    request = NS(
        activity_id=activity,
        generation=0,
        input_payload={"tool_call": call, "catalog_fingerprint": "catalog"},
        input_digest="digest",
        input_ref=None,
    )
    task = NS(
        owner_user_id="owner",
        team_id=None,
        request_payload=request.input_payload,
        request_digest="digest",
        request_ref=None,
        status="call_started",
        call_started_at=object(),
        request_generation=0,
        claim_generation=1,
        run_id=run,
        request_event_position=1,
    )
    approval = NS(status="approved", subject_activity_id=activity, run_id=run)
    reads = 0

    async def current(_factory, _owner):
        return NS(token_version=3), None, "authorization"

    @asynccontextmanager
    async def factory(_authorization):
        nonlocal reads
        reads += 1

        async def get(model, _key):
            if model.__name__ == "ExecutionActivityTaskORM":
                return task
            if model.__name__ == "ExecutionRunProjectionORM":
                return NS(state={"projected": reads > 1})
            return approval

        yield NS(db_session=NS(get=get))

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(authority, "current", current)
    monkeypatch.setattr(authority.asyncio, "sleep", no_delay)
    monkeypatch.setattr(
        RunState,
        "model_validate",
        staticmethod(
            lambda state: NS(
                started_activity_claims=((activity, 0, 1),) if state["projected"] else (),
            )
        ),
    )
    facts = await authority.verify_started(factory, arm, request, context)
    assert reads == 2
    assert facts["claim_generation"] == 1
