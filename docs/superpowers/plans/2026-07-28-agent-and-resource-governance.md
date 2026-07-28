# Agent and Resource Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce strict Ask read-only behavior, transactional Agent tool execution, single task outcomes, and the shared resource-version/build/binding foundation used by knowledge-base and codebase plans.

**Architecture:** Add declarative tool policies and a preflighted batch executor around existing agents, then replace event-inferred completion with explicit `RunOutcome`. Add generic build, event, approval, and session-binding persistence without merging KB and Codebase domain pipelines; domain-specific version providers are supplied by the follow-up plans.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy async, Alembic, PostgreSQL/JSONB, Redis Streams, pytest, Next.js/TypeScript.

## Global Constraints

- Ask permits only knowledge/code search, source reads, `message`, and administrator-approved read-only MCP/A2A functions.
- A child Agent inherits the parent `CapabilityPolicy` and can only narrow it.
- A tool batch performs no side effect until every call has passed capability, RBAC, argument, approval, idempotency, and concurrency preflight.
- Non-idempotent and unknown side effects are never retried automatically.
- A run has exactly one of `succeeded`, `failed`, `cancelled`, or `waiting`; terminal states cannot be overwritten.
- Database and API changes are additive; legacy session resource fields remain readable for at least one compatibility release.
- Existing user changes in the worktree must not be reformatted, reverted, or included in task commits.

---

## File Structure

### New domain and service files

- `api/app/domain/models/tool_policy.py` — tool effect, idempotency, approval, and concurrency metadata.
- `api/app/domain/models/run_outcome.py` — explicit run status and error contract.
- `api/app/domain/models/resource_governance.py` — resource kind, build, build event, and session binding domain types.
- `api/app/domain/models/tool_approval.py` — persisted approval batch/call state.
- `api/app/domain/repositories/resource_governance_repository.py` — build, binding, event, and approval persistence protocol.
- `api/app/domain/services/tools/capability_policy.py` — Ask/Agent capability filtering.
- `api/app/domain/services/agents/tool_batch_executor.py` — whole-batch preflight and ordered execution.
- `api/app/domain/services/session_flow_resolver.py` — one routing decision for all resource/mode combinations.
- `api/app/application/services/resource_guard_service.py` — shared scope, readiness, mode, and version checks.
- `api/app/application/services/resource_binding_service.py` — create/upgrade/list binding operations.
- `api/app/interfaces/endpoints/resource_governance_routes.py` — binding upgrade, build query, and event replay endpoints.
- `api/app/infrastructure/models/resource_governance.py` — SQLAlchemy models.
- `api/app/infrastructure/repositories/db_resource_governance_repository.py` — PostgreSQL repository.
- `api/alembic/versions/b5c6d7e8f9a0_add_integration_tool_policies.py` — administrator-owned MCP/A2A function policies.
- `api/alembic/versions/b6c7d8e9f0a1_add_resource_governance_foundation.py` — additive shared schema.

### Existing files with focused modifications

- `api/app/domain/services/tools/base.py` — attach `ToolExecutionPolicy` to descriptors.
- `api/app/domain/services/tools/tool_registry.py` — policy-aware Ask/Agent registry.
- `api/app/domain/services/tools/{message,search,file,shell,browser,memory,artifact,image_generation,subagent,knowledge_base_tools,codebase_tools,mcp,a2a}.py` — explicit policy declarations.
- `api/app/domain/services/subagent_factory.py` — parent policy inheritance.
- `api/app/application/services/task_runner_factory.py` — construct policy once and stop passing unsafe Ask extras.
- `api/app/domain/services/agent_task_runner.py` — route through resolver and consume `RunOutcome`.
- `api/app/domain/services/flows/{base,doc_qa_flow,code_ask_flow,hybrid_ask_flow,planner_react}.py` — publish outcomes.
- `api/app/domain/services/agents/base.py` — delegate batch execution and policy-aware retry.
- `api/app/interfaces/endpoints/session_routes.py` — approval batch payload and resource binding API compatibility.
- `api/app/application/services/{session_service,agent_service}.py` — remove duplicate mode coercion.
- `api/app/infrastructure/external/task/{task_state,redis_stream_task}.py` and `api/app/worker/main.py` — execution generation, duplicate ack, and common reconciliation.
- `api/app/domain/repositories/uow.py` and `api/app/infrastructure/repositories/db_uow.py` — expose governance repository.
- `api/app/infrastructure/models/__init__.py` — register new ORM models.
- `api/app/main.py` — register governance router.

## Task 1: Add Tool Execution Policy Metadata

**Files:**

- Create: `api/app/domain/models/tool_policy.py`
- Modify: `api/app/domain/services/tools/base.py:17-117`
- Test: `api/tests/app/domain/services/tools/test_tool_policy.py`

**Interfaces:**

- Produces: `ToolCapability`, `ToolEffect`, `ToolIdempotency`, `ApprovalMode`, `ToolExecutionPolicy`, `ToolDescriptor`.
- Produces: `BaseTool.get_tool_descriptor(name: str) -> ToolDescriptor`.
- Consumes: none.

- [ ] **Step 1: Write the failing metadata tests**

```python
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.services.tools.base import BaseTool, tool


class ReadTool(BaseTool):
    name = "read"

    @tool(
        name="read_value",
        description="read",
        parameters={},
        required=[],
        policy=ToolExecutionPolicy(
            capability=ToolCapability.MESSAGE,
            effect=ToolEffect.READ_ONLY,
            idempotency=ToolIdempotency.SAFE,
            approval=ApprovalMode.NEVER,
        ),
    )
    async def read_value(self):
        return "ok"


def test_tool_descriptor_exposes_execution_policy():
    descriptor = ReadTool().get_tool_descriptor("read_value")
    assert descriptor.policy.effect == ToolEffect.READ_ONLY
    assert descriptor.policy.idempotency == ToolIdempotency.SAFE


def test_missing_policy_is_conservative():
    class LegacyTool(BaseTool):
        name = "legacy"

        @tool(name="legacy_call", description="legacy", parameters={}, required=[])
        async def legacy_call(self):
            return "ok"

    policy = LegacyTool().get_tool_descriptor("legacy_call").policy
    assert policy.capability == ToolCapability.UNKNOWN
    assert policy.effect == ToolEffect.INTERACTIVE
    assert policy.idempotency == ToolIdempotency.UNKNOWN
    assert policy.approval == ApprovalMode.ALWAYS
```

- [ ] **Step 2: Run the tests and verify the missing API fails**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/tools/test_tool_policy.py -q`

Expected: FAIL because `tool_policy` and `get_tool_descriptor` do not exist.

- [ ] **Step 3: Add the policy types and descriptor lookup**

```python
class ToolCapability(str, Enum):
    MESSAGE = "message"
    KNOWLEDGE_READ = "knowledge_read"
    CODE_READ = "code_read"
    INTEGRATION_READ = "integration_read"
    WEB_READ = "web_read"
    GENERATION = "generation"
    EXECUTION = "execution"
    UNKNOWN = "unknown"


class ToolEffect(str, Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    EXTERNAL_WRITE = "external_write"
    INTERACTIVE = "interactive"


class ToolIdempotency(str, Enum):
    SAFE = "safe"
    IDEMPOTENT_WITH_KEY = "idempotent_with_key"
    NON_IDEMPOTENT = "non_idempotent"
    UNKNOWN = "unknown"


class ApprovalMode(str, Enum):
    NEVER = "never"
    POLICY = "policy"
    ALWAYS = "always"


class ToolExecutionPolicy(BaseModel):
    capability: ToolCapability
    effect: ToolEffect
    idempotency: ToolIdempotency
    approval: ApprovalMode
    concurrency_group: str = "none"


CONSERVATIVE_TOOL_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.UNKNOWN,
    effect=ToolEffect.INTERACTIVE,
    idempotency=ToolIdempotency.UNKNOWN,
    approval=ApprovalMode.ALWAYS,
    concurrency_group="unknown",
)
```

Extend `tool()` with `policy: ToolExecutionPolicy = CONSERVATIVE_TOOL_POLICY`, attach `_tool_policy`, and have `BaseTool.get_tool_descriptor()` combine the existing schema, bound method, tool-pack name, and policy.

- [ ] **Step 4: Run focused and existing base-tool tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/tools/test_tool_policy.py tests/app/domain/services/tools/test_tool_registry.py -q`

Expected: all tests PASS; legacy decorators retain conservative behavior.

- [ ] **Step 5: Commit only Task 1 files**

```bash
git add api/app/domain/models/tool_policy.py api/app/domain/services/tools/base.py api/tests/app/domain/services/tools/test_tool_policy.py
git commit -m "feat(agent): declare tool execution policies"
```

## Task 2: Enforce Capability Policies in Ask, Agent, Integrations, and Subagents

**Files:**

- Create: `api/app/domain/services/tools/capability_policy.py`
- Modify: `api/app/domain/services/tools/tool_registry.py`
- Modify: `api/app/application/services/task_runner_factory.py`
- Modify: `api/app/domain/services/subagent_factory.py`
- Modify: `api/app/domain/services/tools/mcp.py`
- Modify: `api/app/domain/services/tools/a2a.py`
- Modify: `api/app/domain/models/integration_server.py`
- Modify: `api/app/domain/models/app_config.py`
- Modify: `api/app/infrastructure/models/integration_server.py`
- Modify: `api/app/domain/utils/integration_config_builder.py`
- Create: `api/alembic/versions/b5c6d7e8f9a0_add_integration_tool_policies.py`
- Modify: all built-in tool files listed in the File Structure
- Test: `api/tests/app/domain/services/tools/test_capability_policy.py`
- Test: `api/tests/app/domain/services/test_subagent_factory.py`
- Test: `api/tests/app/application/services/test_task_runner_factory_ask_policy.py`

**Interfaces:**

- Consumes: `ToolExecutionPolicy` from Task 1.
- Produces: `CapabilityPolicy.for_mode(mode: SessionMode)`.
- Produces: `ToolRegistry.build_tools(policy: CapabilityPolicy, ...)`.
- Produces: MCP/A2A per-function policy metadata with conservative defaults.

- [ ] **Step 1: Write failing Ask escape tests**

```python
def test_ask_exposes_only_read_only_descriptors(tool_fixture):
    tools = ToolRegistry.build_tools(
        policy=CapabilityPolicy.for_mode(SessionMode.ASK),
        candidate_tools=tool_fixture.all_tools,
    )
    names = {d.name for tool in tools for d in tool.get_tool_descriptors()}
    assert {"kb_search", "get_document", "semantic_search", "read_code"} <= names
    assert not {"write_file", "shell_execute", "browser_click", "artifact_write", "delegate_subtask"} & names


@pytest.mark.asyncio
async def test_ask_subagent_cannot_expand_parent_policy(subagent_factory):
    with pytest.raises(CapabilityDeniedError):
        await subagent_factory.create(
            parent_policy=CapabilityPolicy.for_mode(SessionMode.ASK),
            requested_tool_names=["shell_execute"],
        )


def test_unknown_mcp_function_is_hidden_from_ask(mcp_tool):
    mcp_tool.register_schema("create_ticket", schema={}, policy=None)
    assert "create_ticket" not in mcp_tool.schemas_for(CapabilityPolicy.for_mode(SessionMode.ASK))
```

- [ ] **Step 2: Run the focused tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/tools/test_capability_policy.py tests/app/application/services/test_task_runner_factory_ask_policy.py -q`

Expected: FAIL because tool selection is not policy-aware.

- [ ] **Step 3: Implement policy intersection and classify built-ins**

Use these exact classifications:

```python
READ_SAFE = ToolExecutionPolicy(
    capability=ToolCapability.KNOWLEDGE_READ,
    effect=ToolEffect.READ_ONLY,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.NEVER,
)
WORKSPACE_WRITE = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.WORKSPACE_WRITE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.POLICY,
    concurrency_group="filesystem",
)
EXTERNAL_WRITE = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.EXTERNAL_WRITE,
    idempotency=ToolIdempotency.UNKNOWN,
    approval=ApprovalMode.ALWAYS,
    concurrency_group="integration",
)
INTERACTIVE_BROWSER = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.INTERACTIVE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.POLICY,
    concurrency_group="browser",
)
```

Classify Message as `MESSAGE`, KB reads as `KNOWLEDGE_READ`, Codebase reads as `CODE_READ`, and administrator-approved read-only integration functions as `INTEGRATION_READ`. Generic web Search/Vision reads are `WEB_READ` and are not available in Ask. File writes and Artifact writes are `WORKSPACE_WRITE`; Shell and browser mutation are interactive/non-idempotent; Memory save, ImageGeneration, and unknown integrations are write/unknown. Dynamic MCP/A2A functions read an administrator-owned `tool_policies` map; absent entries use `CONSERVATIVE_TOOL_POLICY`.

Add a JSONB `tool_policies` column to both integration server tables in migration `b5c6d7e8f9a0` with `down_revision = "a5b6c7d8e9f0"`. Domain/config records expose `tool_policies: dict[str, ToolExecutionPolicy]`. Only admin create/update endpoints may change this map; tenant users can read the non-secret declarations. MCP's existing `extra` field is not authoritative because it is an untyped extension bag.

`CapabilityPolicy.allows()` must return true for Ask only when `effect == READ_ONLY` and capability is one of `MESSAGE`, `KNOWLEDGE_READ`, `CODE_READ`, or `INTEGRATION_READ`; Agent returns true after tenant/tool allowlist checks. Pass the same immutable policy object into subagent creation and intersect requested tools with it.

- [ ] **Step 4: Run policy, Ask-flow, registry, and subagent tests**

Run: `cd api && .venv/bin/alembic upgrade b5c6d7e8f9a0 && .venv/bin/pytest tests/app/domain/services/tools/test_capability_policy.py tests/app/domain/services/tools/test_tool_registry.py tests/app/domain/services/flows/test_ask_flows.py tests/app/domain/services/test_subagent_factory.py tests/app/application/services/test_task_runner_factory_ask_policy.py -q`

Expected: PASS, including direct and delegated Ask write-denial tests.

- [ ] **Step 5: Commit the capability boundary**

```bash
git add api/alembic/versions/b5c6d7e8f9a0_add_integration_tool_policies.py api/app/domain/models/integration_server.py api/app/domain/models/app_config.py api/app/infrastructure/models/integration_server.py api/app/domain/utils/integration_config_builder.py api/app/domain/services/tools api/app/domain/services/subagent_factory.py api/app/application/services/task_runner_factory.py api/tests/app/domain/services/tools api/tests/app/domain/services/flows/test_ask_flows.py api/tests/app/domain/services/test_subagent_factory.py api/tests/app/application/services/test_task_runner_factory_ask_policy.py
git commit -m "feat(agent): enforce mode capability boundaries"
```

## Task 3: Centralize Session Flow Resolution and Enable Knowledge-Base Agent Mode

**Files:**

- Create: `api/app/domain/services/session_flow_resolver.py`
- Modify: `api/app/application/services/session_service.py:53-106`
- Modify: `api/app/application/services/agent_service.py:267-294`
- Modify: `api/app/domain/services/agent_task_runner.py:151-228`
- Modify: `api/app/application/services/task_runner_factory.py:381-425`
- Modify: `ui/src/components/knowledge/knowledge-library.tsx:225-235,339-369`
- Test: `api/tests/app/domain/services/test_session_flow_resolver.py`
- Test: `api/tests/app/application/services/test_session_service.py`
- Test: `ui/src/components/knowledge/knowledge-library.test.tsx`

**Interfaces:**

- Consumes: `CapabilityPolicy.for_mode()`.
- Produces: `FlowKind` and `SessionFlowDecision`.
- Produces: `SessionFlowResolver.resolve(mode, has_kb, has_codebase)`.

- [ ] **Step 1: Write the routing table tests**

```python
@pytest.mark.parametrize(
    ("mode", "has_kb", "has_codebase", "expected"),
    [
        (SessionMode.ASK, True, False, FlowKind.DOC_ASK),
        (SessionMode.AGENT, True, False, FlowKind.PLANNER_REACT),
        (SessionMode.ASK, False, True, FlowKind.CODE_ASK),
        (SessionMode.AGENT, False, True, FlowKind.PLANNER_REACT),
        (SessionMode.ASK, True, True, FlowKind.HYBRID_ASK),
        (SessionMode.AGENT, True, True, FlowKind.PLANNER_REACT),
        (SessionMode.AGENT, False, False, FlowKind.PLANNER_REACT),
    ],
)
def test_flow_matrix(mode, has_kb, has_codebase, expected):
    assert SessionFlowResolver.resolve(mode, has_kb, has_codebase).flow_kind == expected
```

Add a UI test asserting both “start Ask” and “start Agent” buttons call `createSession` with their respective modes.

- [ ] **Step 2: Run routing and UI tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/test_session_flow_resolver.py tests/app/application/services/test_session_service.py -q`

Run: `cd ui && npm test -- --run src/components/knowledge/knowledge-library.test.tsx`

Expected: backend test FAILS because resolver is absent; UI test FAILS because Agent entry is absent.

- [ ] **Step 3: Add one resolver and remove both KB Agent coercions**

```python
class FlowKind(str, Enum):
    DOC_ASK = "doc_ask"
    CODE_ASK = "code_ask"
    HYBRID_ASK = "hybrid_ask"
    PLANNER_REACT = "planner_react"


class SessionFlowResolver:
    @staticmethod
    def resolve(mode: SessionMode, has_kb: bool, has_codebase: bool) -> SessionFlowDecision:
        if mode == SessionMode.AGENT:
            return SessionFlowDecision(FlowKind.PLANNER_REACT, CapabilityPolicy.for_mode(mode))
        if has_kb and has_codebase:
            return SessionFlowDecision(FlowKind.HYBRID_ASK, CapabilityPolicy.for_mode(mode))
        if has_codebase:
            return SessionFlowDecision(FlowKind.CODE_ASK, CapabilityPolicy.for_mode(mode))
        if has_kb:
            return SessionFlowDecision(FlowKind.DOC_ASK, CapabilityPolicy.for_mode(mode))
        return SessionFlowDecision(FlowKind.PLANNER_REACT, CapabilityPolicy.for_mode(mode))
```

Delete mode rewriting in SessionService and AgentService. Make AgentTaskRunner instantiate from the resolver result. Apply `DOC_AGENT_SKILL_PROMPT` whenever mode is Agent and a KB is bound, including KB-only sessions.

- [ ] **Step 4: Run routing, service, runner, and UI tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/test_session_flow_resolver.py tests/app/application/services/test_session_service.py tests/app/domain/services/test_agent_task_runner_routing.py -q`

Run: `cd ui && npm test -- --run src/components/knowledge/knowledge-library.test.tsx`

Expected: PASS; KB-only Agent resolves to PlannerReAct and retains Agent mode.

- [ ] **Step 5: Commit the routing decision**

```bash
git add api/app/domain/services/session_flow_resolver.py api/app/application/services/session_service.py api/app/application/services/agent_service.py api/app/domain/services/agent_task_runner.py api/app/application/services/task_runner_factory.py api/tests/app/domain/services/test_session_flow_resolver.py api/tests/app/application/services/test_session_service.py api/tests/app/domain/services/test_agent_task_runner_routing.py ui/src/components/knowledge/knowledge-library.tsx ui/src/components/knowledge/knowledge-library.test.tsx
git commit -m "feat(agent): centralize flow routing and enable kb agent"
```

## Task 4: Persist Approval Batches and Calls

**Files:**

- Create: `api/app/domain/models/tool_approval.py`
- Create: `api/app/domain/repositories/resource_governance_repository.py`
- Create: `api/app/infrastructure/models/resource_governance.py`
- Create: `api/app/infrastructure/repositories/db_resource_governance_repository.py`
- Create: `api/alembic/versions/b6c7d8e9f0a1_add_resource_governance_foundation.py`
- Modify: `api/app/domain/repositories/uow.py`
- Modify: `api/app/infrastructure/repositories/db_uow.py`
- Modify: `api/app/infrastructure/models/__init__.py`
- Test: `api/tests/app/infrastructure/repositories/test_db_tool_approval_repository.py`
- Test: `api/tests/app/alembic/test_resource_governance_migration.py`

**Interfaces:**

- Produces: `ToolApprovalBatch`, `ToolApprovalCall`, `ApprovalStatus`.
- Produces repository methods `save_approval_batch`, `get_pending_approval_batch`, `decide_approval_call`, `consume_approval_batch`.
- Also creates empty shared tables needed by Tasks 9 and 11: `resource_builds`, `session_resource_bindings`, `resource_build_events`.

- [ ] **Step 1: Write repository contract tests**

```python
@pytest.mark.asyncio
async def test_approval_batch_preserves_all_calls(repo):
    batch = ToolApprovalBatch.for_calls(
        session_id="s1",
        calls=[
            ApprovalCallInput("tc1", "browser_click", {"selector": "#buy"}, 0),
            ApprovalCallInput("tc2", "send_message", {"channel": "ops"}, 1),
        ],
    )
    await repo.save_approval_batch(batch)
    loaded = await repo.get_pending_approval_batch("s1")
    assert [call.tool_call_id for call in loaded.calls] == ["tc1", "tc2"]


@pytest.mark.asyncio
async def test_approval_decision_is_idempotent(repo):
    first = await repo.decide_approval_call("tc1", ApprovalStatus.APPROVED, "u1")
    second = await repo.decide_approval_call("tc1", ApprovalStatus.APPROVED, "u1")
    assert first.decided_at == second.decided_at
```

- [ ] **Step 2: Run repository tests**

Run: `cd api && .venv/bin/pytest tests/app/infrastructure/repositories/test_db_tool_approval_repository.py tests/app/alembic/test_resource_governance_migration.py -q`

Expected: FAIL because schema and repository do not exist.

- [ ] **Step 3: Add the additive migration and repository**

Create:

```text
tool_approval_batches(id, session_id, status, expires_at, created_at, decided_at)
tool_approval_calls(
  id, batch_id, tool_call_id, ordinal, tool_name, normalized_args,
  args_hash, capability, effect, idempotency, approval, concurrency_group,
  status, decided_by, decided_at
)
resource_builds(
  id, resource_kind, resource_id, version_id, parent_version_id,
  command_key, state, phase, progress, capabilities jsonb,
  degraded_reasons jsonb, metrics jsonb, error_code, error_message,
  heartbeat_at, last_event_seq, created_by, created_at, started_at, finished_at
)
session_resource_bindings(
  id, session_id, resource_kind, resource_id, version_id,
  is_current, supersedes_binding_id, bound_by, created_at
)
resource_build_events(
  id, build_id, seq, phase, state, progress, payload jsonb, created_at
)
```

Constraints:

- unique `tool_approval_calls(tool_call_id)`;
- unique `tool_approval_calls(batch_id, ordinal)`;
- index pending batches by `(session_id, status, created_at)`.
- partial unique active build key `(resource_kind, resource_id)` where state is `queued` or `running`;
- partial unique current binding key `(session_id, resource_kind)` where `is_current=true`;
- unique event cursor `(build_id, seq)`.

The shared resource IDs are intentionally polymorphic at this layer; domain migrations own the knowledge/codebase foreign keys. Add `resource_governance` to both UoW classes. Use `down_revision = "b5c6d7e8f9a0"` so integration policy metadata always precedes governance persistence.

- [ ] **Step 4: Upgrade a temporary database and run repository tests**

Run: `cd api && .venv/bin/alembic upgrade head`

Run: `cd api && .venv/bin/pytest tests/app/infrastructure/repositories/test_db_tool_approval_repository.py tests/app/alembic/test_resource_governance_migration.py -q`

Expected: migration succeeds and tests PASS.

- [ ] **Step 5: Commit approval persistence**

```bash
git add api/alembic/versions/b6c7d8e9f0a1_add_resource_governance_foundation.py api/app/domain/models/tool_approval.py api/app/domain/repositories/resource_governance_repository.py api/app/infrastructure/models/resource_governance.py api/app/infrastructure/repositories/db_resource_governance_repository.py api/app/domain/repositories/uow.py api/app/infrastructure/repositories/db_uow.py api/app/infrastructure/models/__init__.py api/tests/app/infrastructure/repositories/test_db_tool_approval_repository.py api/tests/app/alembic/test_resource_governance_migration.py
git commit -m "feat(agent): persist tool approval batches"
```

## Task 5: Preflight Entire Tool Batches Before Execution

**Files:**

- Create: `api/app/domain/services/agents/tool_batch_executor.py`
- Modify: `api/app/domain/services/agents/base.py:1060-1217,1361-1490`
- Modify: `api/app/interfaces/endpoints/session_routes.py:87-136`
- Modify: `api/app/domain/services/agents/react.py:300-350`
- Test: `api/tests/app/domain/services/agents/test_tool_batch_executor.py`
- Test: `api/tests/app/interfaces/endpoints/test_tool_approval_batch_routes.py`

**Interfaces:**

- Consumes: Task 1 policies, Task 2 CapabilityPolicy, Task 4 repository.
- Produces: `PreparedToolCall`, `PreparedToolBatch`, `ToolBatchExecutionResult`.
- Produces: `ToolBatchExecutor.preflight()` and `execute()`.

- [ ] **Step 1: Write failing zero-side-effect and queue tests**

```python
@pytest.mark.asyncio
async def test_gated_batch_executes_no_side_effect_before_approval(executor, calls, recorder):
    batch = await executor.preflight([
        calls.write("tc1", "write_file"),
        calls.gated("tc2", "browser_click"),
    ])
    result = await executor.execute(batch)
    assert result.waiting is True
    assert recorder.invocations == []
    assert [c.tool_call_id for c in result.approval_batch.calls] == ["tc1", "tc2"]


@pytest.mark.asyncio
async def test_read_only_calls_parallelize_but_filesystem_calls_serialize(executor, recorder):
    await executor.execute_approved([
        recorder.call("r1", effect="read_only", delay=0.01),
        recorder.call("r2", effect="read_only", delay=0.01),
        recorder.call("w1", group="filesystem"),
        recorder.call("w2", group="filesystem"),
    ])
    assert recorder.max_parallel_reads == 2
    assert recorder.max_parallel_by_group["filesystem"] == 1


@pytest.mark.asyncio
async def test_expired_approval_batch_executes_nothing(executor, expired_batch, recorder):
    result = await executor.resume(expired_batch.id, actor_id="u1")
    assert result.rejected_reason == "approval_expired"
    assert recorder.invocations == []
```

- [ ] **Step 2: Run executor tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/agents/test_tool_batch_executor.py tests/app/interfaces/endpoints/test_tool_approval_batch_routes.py -q`

Expected: FAIL because current BaseAgent executes per-call gates inside `gather`.

- [ ] **Step 3: Move validation and gating into ToolBatchExecutor**

Use these dataclasses:

```python
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
```

`preflight()` resolves every tool and arguments, checks CapabilityPolicy, computes gates, and persists one batch before returning. `execute()` returns `WaitEvent` without calling any effectful tool if approval is pending. After approval, group calls by concurrency group; run read-only `none` calls concurrently and serialize every non-`none` group. Reject expired batches before consuming any call. Remove `_enter_tool_approval_gate()` writes to `pending_metadata`; keep a compatibility projection containing only `approval_batch_id`.

- [ ] **Step 4: Run executor, ReAct resume, and HITL route tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/agents/test_tool_batch_executor.py tests/app/domain/services/agents/test_base_tool_gating.py tests/app/domain/services/flows/test_planner_react_failed_resume.py tests/app/interfaces/endpoints/test_tool_approval_batch_routes.py -q`

Expected: PASS; a gated mixed batch records zero effectful invocations before approval.

- [ ] **Step 5: Commit atomic tool-batch gating**

```bash
git add api/app/domain/services/agents/tool_batch_executor.py api/app/domain/services/agents/base.py api/app/domain/services/agents/react.py api/app/interfaces/endpoints/session_routes.py api/tests/app/domain/services/agents api/tests/app/interfaces/endpoints/test_tool_approval_batch_routes.py
git commit -m "feat(agent): preflight tool batches before execution"
```

## Task 6: Apply Idempotency-Aware Tool Retries

**Files:**

- Modify: `api/app/domain/services/agents/tool_batch_executor.py`
- Modify: `api/app/domain/services/agents/base.py:727-777`
- Modify: `api/app/domain/models/tool_result.py`
- Create: `api/app/domain/models/tool_execution.py`
- Test: `api/tests/app/domain/services/agents/test_tool_retry_policy.py`

**Interfaces:**

- Consumes: `ToolIdempotency`.
- Produces: `ToolExecutionAttempt`, `ToolExecutionStatus.OUTCOME_UNKNOWN`.
- Produces: stable `idempotency_key = sha256(session_id + tool_call_id + args_hash)`.

- [ ] **Step 1: Write retry behavior tests**

```python
@pytest.mark.asyncio
async def test_non_idempotent_timeout_is_not_retried(executor, timeout_tool):
    result = await executor.invoke(timeout_tool.non_idempotent_call())
    assert timeout_tool.calls == 1
    assert result.status == ToolExecutionStatus.OUTCOME_UNKNOWN


@pytest.mark.asyncio
async def test_read_only_transient_failure_is_bounded_retried(executor, flaky_read_tool):
    result = await executor.invoke(flaky_read_tool.call())
    assert flaky_read_tool.calls == 2
    assert result.success is True


@pytest.mark.asyncio
async def test_idempotent_write_reuses_same_key(executor, flaky_idempotent_tool):
    await executor.invoke(flaky_idempotent_tool.call())
    assert len(set(flaky_idempotent_tool.received_keys)) == 1
```

- [ ] **Step 2: Run retry tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/agents/test_tool_retry_policy.py -q`

Expected: FAIL because `_invoke_tool` retries every timeout and exception.

- [ ] **Step 3: Replace global retry loop with policy branches**

```python
if policy.idempotency in {ToolIdempotency.NON_IDEMPOTENT, ToolIdempotency.UNKNOWN}:
    max_attempts = 1
elif policy.idempotency == ToolIdempotency.IDEMPOTENT_WITH_KEY:
    max_attempts = configured_max_attempts
    normalized_args["idempotency_key"] = stable_key
else:
    max_attempts = configured_max_attempts
```

Only retry errors classified as transient. A timeout on external/write/interactive returns `OUTCOME_UNKNOWN`; a safe read timeout returns a normal failed result after its bounded attempts. Persist attempt number, key, timing, and result summary through existing audit logging without storing sensitive full arguments.

- [ ] **Step 4: Run retry and existing tool tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/agents/test_tool_retry_policy.py tests/app/domain/services/tools tests/app/domain/services/agents/test_tool_batch_executor.py -q`

Expected: PASS; non-idempotent call count remains one.

- [ ] **Step 5: Commit retry governance**

```bash
git add api/app/domain/models/tool_execution.py api/app/domain/models/tool_result.py api/app/domain/services/agents/tool_batch_executor.py api/app/domain/services/agents/base.py api/tests/app/domain/services/agents/test_tool_retry_policy.py
git commit -m "feat(agent): make tool retries idempotency aware"
```

## Task 7: Introduce Explicit RunOutcome and Enforce One Terminal State

**Files:**

- Create: `api/app/domain/models/run_outcome.py`
- Modify: `api/app/domain/services/flows/base.py`
- Modify: `api/app/domain/services/flows/doc_qa_flow.py`
- Modify: `api/app/domain/services/flows/code_ask_flow.py`
- Modify: `api/app/domain/services/flows/hybrid_ask_flow.py`
- Modify: `api/app/domain/services/flows/planner_react.py`
- Modify: `api/app/domain/services/agent_task_runner.py:380-628`
- Modify: `api/app/domain/services/agent/event_emitter.py`
- Modify: `api/app/infrastructure/external/task/redis_stream_task.py:104-124`
- Modify: `ui/src/lib/session-events.ts`
- Test: `api/tests/app/domain/services/test_agent_task_runner_outcome.py`
- Test: `api/tests/app/domain/services/flows/test_flow_outcomes.py`
- Test: `ui/src/lib/session-events.test.ts`

**Interfaces:**

- Produces: `RunStatus`, `RunError`, `RunOutcome`.
- Produces: `BaseFlow.outcome: RunOutcome`.
- Consumes: existing events as presentation only.

- [ ] **Step 1: Write failing terminal-state tests**

```python
@pytest.mark.asyncio
async def test_error_outcome_never_emits_completed(runner, failing_flow):
    await runner.invoke(task_for(failing_flow))
    statuses = persisted_session_statuses()
    assert statuses == [SessionStatus.RUNNING, SessionStatus.FAILED]


@pytest.mark.asyncio
async def test_waiting_outcome_is_not_task_done(runner, waiting_flow):
    await runner.invoke(task_for(waiting_flow))
    assert runner.terminal_status == SessionStatus.WAITING
    assert await task_state.get_status(runner.task_id) == TaskStatus.PENDING


def test_terminal_reducer_ignores_late_completed():
    state = reduce_events(["running", "failed", "completed"])
    assert state.status == "failed"
```

- [ ] **Step 2: Run backend and UI terminal tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/test_agent_task_runner_outcome.py tests/app/domain/services/flows/test_flow_outcomes.py -q`

Run: `cd ui && npm test -- --run src/lib/session-events.test.ts`

Expected: new backend tests FAIL with completed-after-error behavior.

- [ ] **Step 3: Implement outcome-driven completion**

```python
class RunStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING = "waiting"


class RunOutcome(BaseModel):
    status: RunStatus
    error: RunError | None = None
    usage: dict[str, int | float] = Field(default_factory=dict)
```

Every Flow initializes to failed-safe and explicitly sets its outcome. Ask exceptions set FAILED; IntegrityError from token accounting is handled inside TokenAccountant, not by marking the whole Flow done. AgentTaskRunner maps outcomes to session/task status. Add an emitter guard that atomically rejects a second terminal SessionStatusEvent. UI reducer keeps the first persisted terminal state.

- [ ] **Step 4: Run Flow, runner, event-mapper, task, and UI suites**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/flows tests/app/domain/services/test_agent_task_runner_outcome.py tests/app/domain/services/test_agent_task_runner_on_done.py tests/app/interfaces/schemas/test_event_mapper.py -q`

Run: `cd ui && npm test -- --run src/lib/session-events.test.ts`

Expected: PASS; no test observes failed→completed.

- [ ] **Step 5: Commit explicit outcomes**

```bash
git add api/app/domain/models/run_outcome.py api/app/domain/services/flows api/app/domain/services/agent_task_runner.py api/app/domain/services/agent/event_emitter.py api/app/infrastructure/external/task/redis_stream_task.py api/tests/app/domain/services ui/src/lib/session-events.ts ui/src/lib/session-events.test.ts
git commit -m "feat(agent): enforce explicit single run outcomes"
```

## Task 8: Add Dispatch Generations and Safe Duplicate Handling

**Files:**

- Modify: `api/app/domain/external/task_state_port.py`
- Modify: `api/app/infrastructure/external/task/task_state.py`
- Modify: `api/app/infrastructure/external/task/redis_stream_task.py`
- Modify: `api/app/worker/main.py:249-406`
- Test: `api/tests/app/infrastructure/external/task/test_task_generation.py`
- Test: `api/tests/app/worker/test_worker_duplicate_dispatch.py`

**Interfaces:**

- Produces: `dispatch(task_id, session_id, run_generation)`.
- Produces: compare-and-set `set_status(task_id, generation, status) -> bool`.
- Produces: duplicate claim decisions `ACK_DUPLICATE`, `EXECUTE`, `REQUEUE`.

- [ ] **Step 1: Write failing duplicate and stale-generation tests**

```python
@pytest.mark.asyncio
async def test_lease_conflict_duplicate_is_acked(worker, task_state):
    task_state.lease_owner = "worker-a"
    await worker.handle_claimed("msg-1", "task-1", "session-1", generation=2)
    assert task_state.acked == ["msg-1"]
    assert task_state.executions == []


@pytest.mark.asyncio
async def test_old_generation_cannot_overwrite_new_status(task_state):
    await task_state.register_task("t1", "s1", run_generation=3)
    changed = await task_state.set_status("t1", 2, TaskStatus.FAILED)
    assert changed is False
    assert await task_state.get_status("t1") == TaskStatus.PENDING
```

- [ ] **Step 2: Run generation tests**

Run: `cd api && .venv/bin/pytest tests/app/infrastructure/external/task/test_task_generation.py tests/app/worker/test_worker_duplicate_dispatch.py -q`

Expected: FAIL because dispatch messages and status writes have no generation.

- [ ] **Step 3: Add generation to task metadata and dispatch messages**

Increment generation only when recovery creates a new execution attempt. Include it in Redis stream fields. On claim:

```python
if claimed_generation < current_generation:
    await task_state.ack_dispatch(message_id)
    return
if lease_owner:
    await task_state.ack_dispatch(message_id)
    return
```

All status and heartbeat writes use a Lua compare-and-set against current generation. Do not acknowledge a genuinely recoverable message that has no lease and matches current generation until execution/mark-failure completes.

- [ ] **Step 4: Run task-state, worker recovery, and Agent outcome tests**

Run: `cd api && .venv/bin/pytest tests/app/infrastructure/external/task tests/app/worker tests/app/domain/services/test_agent_task_runner_outcome.py -q`

Expected: PASS; stale messages cannot execute or change current state.

- [ ] **Step 5: Commit dispatch generation controls**

```bash
git add api/app/domain/external/task_state_port.py api/app/infrastructure/external/task/task_state.py api/app/infrastructure/external/task/redis_stream_task.py api/app/worker/main.py api/tests/app/infrastructure/external/task api/tests/app/worker
git commit -m "fix(worker): make dispatch generations duplicate safe"
```

## Task 9: Implement Shared Builds, Bindings, and Version Provider Contracts

**Files:**

- Create: `api/app/domain/models/resource_governance.py`
- Extend: `api/app/domain/repositories/resource_governance_repository.py`
- Extend: `api/app/infrastructure/models/resource_governance.py`
- Extend: `api/app/infrastructure/repositories/db_resource_governance_repository.py`
- Create: `api/app/domain/services/resource_version_provider.py`
- Create: `api/app/application/services/resource_binding_service.py`
- Modify: `api/app/domain/models/session.py`
- Modify: `api/app/infrastructure/repositories/db_session_repository.py`
- Test: `api/tests/app/application/services/test_resource_binding_service.py`
- Test: `api/tests/app/infrastructure/repositories/test_db_resource_governance_repository.py`

**Interfaces:**

- Produces: `ResourceKind`, `BuildState`, `ResourceBuild`, `ResourceBuildEvent`, `SessionResourceBinding`, `PublishedResourceVersion`.
- Produces: `ResourceVersionProvider.resolve_published_version(resource_id, requested_version_id, scope)`.
- Produces: `ResourceBindingService.bind_initial()` and `upgrade()`.

- [ ] **Step 1: Write binding semantics tests with fake providers**

```python
@pytest.mark.asyncio
async def test_initial_binding_resolves_active_version_once(service, kb_provider):
    kb_provider.active = PublishedResourceVersion("kb", "kb1", "kbv1", degraded=False)
    first = await service.bind_initial("s1", ResourceKind.KNOWLEDGE_BASE, "kb1", None, scope)
    kb_provider.active = PublishedResourceVersion("kb", "kb1", "kbv2", degraded=False)
    loaded = await service.current("s1", ResourceKind.KNOWLEDGE_BASE, scope)
    assert first.version_id == loaded.version_id == "kbv1"


@pytest.mark.asyncio
async def test_upgrade_keeps_history_and_changes_current(service):
    old = await service.bind_initial("s1", ResourceKind.CODEBASE, "cb1", "cbv1", scope)
    new = await service.upgrade("s1", ResourceKind.CODEBASE, "cbv2", actor_id="u1", scope=scope)
    assert new.supersedes_binding_id == old.id
    assert await service.current_version_id("s1", ResourceKind.CODEBASE, scope) == "cbv2"
```

- [ ] **Step 2: Run binding tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_resource_binding_service.py tests/app/infrastructure/repositories/test_db_resource_governance_repository.py -q`

Expected: FAIL because shared domain types and service do not exist.

- [ ] **Step 3: Implement polymorphic bindings and provider registry**

Use:

```python
class ResourceVersionProvider(Protocol):
    kind: ResourceKind

    async def resolve_published_version(
        self,
        resource_id: str,
        requested_version_id: str | None,
        scope: OwnerScope,
    ) -> PublishedResourceVersion: ...
```

`session_resource_bindings` stores one current row per `(session_id, resource_kind)` using a partial unique index on `is_current`. Upgrade locks the current row, validates the target through the provider, sets old `is_current=false`, and inserts the new row in one UoW. Add a message/event metadata projection containing the binding IDs used for each turn; do not rewrite prior events.

- [ ] **Step 4: Run repository and service transaction tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_resource_binding_service.py tests/app/infrastructure/repositories/test_db_resource_governance_repository.py tests/app/infrastructure/repositories/test_db_uow.py -q`

Expected: PASS, including concurrent-upgrade uniqueness.

- [ ] **Step 5: Commit shared version bindings**

```bash
git add api/app/domain/models/resource_governance.py api/app/domain/services/resource_version_provider.py api/app/domain/repositories/resource_governance_repository.py api/app/application/services/resource_binding_service.py api/app/infrastructure/models/resource_governance.py api/app/infrastructure/repositories/db_resource_governance_repository.py api/app/domain/models/session.py api/app/infrastructure/repositories/db_session_repository.py api/tests/app/application/services/test_resource_binding_service.py api/tests/app/infrastructure/repositories/test_db_resource_governance_repository.py
git commit -m "feat(resources): add immutable session version bindings"
```

## Task 10: Centralize Resource Access and Session Creation Guards

**Files:**

- Create: `api/app/application/services/resource_guard_service.py`
- Modify: `api/app/application/services/session_service.py:53-106`
- Modify: `api/app/application/services/knowledge_base_service.py:251-275`
- Modify: `api/app/application/services/codebase_service.py:239-259`
- Modify: `api/app/interfaces/endpoints/knowledge_base_routes.py`
- Modify: `api/app/interfaces/endpoints/codebase_routes.py`
- Modify: `api/app/interfaces/endpoints/session_routes.py:305-345`
- Create: `api/app/interfaces/endpoints/resource_governance_routes.py`
- Modify: `api/app/main.py`
- Modify: `ui/src/lib/api/session.ts`
- Create: `ui/src/components/workspace/session-resource-version.tsx`
- Modify: `ui/src/components/workspace/session-context-panel.tsx`
- Test: `api/tests/app/application/services/test_resource_guard_service.py`
- Test: `api/tests/app/interfaces/test_resource_mutation_rbac.py`
- Test: `api/tests/app/interfaces/endpoints/test_session_resource_readiness.py`
- Test: `ui/src/components/workspace/session-resource-version.test.tsx`

**Interfaces:**

- Consumes: ResourceBindingService and version providers.
- Produces: `ResourceGuardService.validate_session_request()`.
- Produces: one path used by generic and resource-specific session APIs.

- [ ] **Step 1: Write failing parity and Auditor tests**

```python
@pytest.mark.parametrize("endpoint", [
    "/api/knowledge-bases/kb1/documents",
    "/api/knowledge-bases/kb1/reindex",
    "/api/codebases/cb1/reanalyze",
])
def test_auditor_cannot_mutate_resource(client_as_auditor, endpoint):
    assert client_as_auditor.post(endpoint, json={}).status_code == 403


@pytest.mark.parametrize("factory", ["generic", "knowledge", "codebase"])
def test_all_session_entrypoints_reject_unpublished_resource(factory, clients):
    assert clients[factory].create_session(resource_status="building").status_code == 400


def test_kb_agent_mode_is_preserved(client, ready_kb):
    data = client.post(f"/api/knowledge-bases/{ready_kb.id}/sessions", json={"mode": "agent"}).json()
    assert data["data"]["mode"] == "agent"
```

Add a UI test:

```tsx
it("upgrades only the current binding and labels historical messages", async () => {
  render(<SessionResourceVersion session={sessionBoundToV1} versions={[v1, v2]} />);
  expect(screen.getByText("v1")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "升级上下文" }));
  await user.click(screen.getByRole("button", { name: "确认升级到 v2" }));
  expect(sessionApi.upgradeResourceBinding).toHaveBeenCalledWith("s1", "knowledge_base", "v2");
  expect(screen.getByTestId("message-old")).toHaveTextContent("v1");
});
```

- [ ] **Step 2: Run guard and route tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_resource_guard_service.py tests/app/interfaces/test_resource_mutation_rbac.py tests/app/interfaces/endpoints/test_session_resource_readiness.py -q`

Run: `cd ui && npm test -- --run src/components/workspace/session-resource-version.test.tsx`

Expected: FAIL on missing guards, readiness parity, KB Agent coercion, and binding upgrade UI.

- [ ] **Step 3: Route every entry through ResourceGuardService**

`validate_session_request()` must:

```python
async def validate_session_request(
    *,
    mode: SessionMode,
    codebase_id: str | None,
    codebase_version_id: str | None,
    knowledge_base_id: str | None,
    knowledge_base_version_id: str | None,
    scope: OwnerScope,
) -> ValidatedSessionResources:
    ...
```

It validates ownership and asks each installed provider for a published version. During the compatibility interval, a provider may return a synthetic `legacy:<resource_id>` version only for migrated v1 resources, never for building/failed resources. Add `require_non_auditor` to all mutation and resource-session routes. Replace mutating `GET /codebases/{id}/download` with `POST /codebases/{id}/snapshots`; keep GET as a deprecated compatibility adapter that does not create a new snapshot.

Expose:

```text
GET  /sessions/{session_id}/resource-bindings
POST /sessions/{session_id}/resource-bindings/{resource_kind}/upgrade
```

The upgrade request requires `target_version_id`; the response returns old/new binding IDs and applies only to future messages. The session header shows current versions and available upgrades. Each historical assistant message renders version labels from its immutable event binding metadata. Do not auto-upgrade.

- [ ] **Step 4: Run RBAC, session, and existing endpoint tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_resource_guard_service.py tests/app/interfaces/test_resource_mutation_rbac.py tests/app/interfaces/endpoints/test_session_resource_readiness.py tests/app/interfaces/endpoints/test_codebase_routes.py tests/app/interfaces/endpoints/test_knowledge_base_routes.py -q`

Run: `cd ui && npm test -- --run src/components/workspace/session-resource-version.test.tsx`

Expected: PASS; generic and specialized creation return the same validation result, and upgrade changes only the current binding.

- [ ] **Step 5: Commit shared guards**

```bash
git add api/app/application/services/resource_guard_service.py api/app/application/services/session_service.py api/app/application/services/knowledge_base_service.py api/app/application/services/codebase_service.py api/app/interfaces/endpoints/knowledge_base_routes.py api/app/interfaces/endpoints/codebase_routes.py api/app/interfaces/endpoints/session_routes.py api/app/interfaces/endpoints/resource_governance_routes.py api/app/main.py api/tests/app/application/services/test_resource_guard_service.py api/tests/app/interfaces/test_resource_mutation_rbac.py api/tests/app/interfaces/endpoints/test_session_resource_readiness.py ui/src/lib/api/session.ts ui/src/components/workspace/session-resource-version.tsx ui/src/components/workspace/session-resource-version.test.tsx ui/src/components/workspace/session-context-panel.tsx
git commit -m "fix(resources): unify mutation and session guards"
```

## Task 11: Persist Resource Build Events and Expose Cursor Replay

**Files:**

- Extend: `api/app/domain/repositories/resource_governance_repository.py`
- Extend: `api/app/infrastructure/repositories/db_resource_governance_repository.py`
- Create: `api/app/application/services/resource_build_service.py`
- Modify: `api/app/interfaces/endpoints/resource_governance_routes.py`
- Modify: `api/app/main.py`
- Test: `api/tests/app/application/services/test_resource_build_service.py`
- Test: `api/tests/app/interfaces/endpoints/test_resource_build_routes.py`

**Interfaces:**

- Consumes: `ResourceBuild` and `ResourceBuildEvent`.
- Produces: `append_event(build_id, event) -> int`.
- Produces: `list_events(build_id, after_seq, limit)`.
- Produces: `GET /api/resource-builds/{build_id}/events?after=<seq>`.

- [ ] **Step 1: Write event ordering and replay tests**

```python
@pytest.mark.asyncio
async def test_build_events_have_monotonic_sequence(service):
    first = await service.append_event("b1", phase="parse", state="running")
    second = await service.append_event("b1", phase="index", state="running")
    assert (first.seq, second.seq) == (1, 2)


def test_build_event_endpoint_replays_after_cursor(client, build_with_events):
    response = client.get(f"/api/resource-builds/{build_with_events.id}/events?after=1")
    assert [item["seq"] for item in response.json()["data"]["events"]] == [2, 3]
```

- [ ] **Step 2: Run build event tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_resource_build_service.py tests/app/interfaces/endpoints/test_resource_build_routes.py -q`

Expected: FAIL because only Redis output streams exist.

- [ ] **Step 3: Implement transactional append and SSE replay**

Lock the build row, allocate `seq = last_event_seq + 1`, insert the event, and update the build heartbeat in one transaction. The endpoint first replays PostgreSQL rows after the cursor, then subscribes to Redis for notification and fetches the newly committed row by sequence. Redis payloads contain only `build_id` and `seq`; PostgreSQL is authoritative.

- [ ] **Step 4: Run service, endpoint, and reconnect tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_resource_build_service.py tests/app/interfaces/endpoints/test_resource_build_routes.py -q`

Expected: PASS; reconnect after Redis stream deletion still replays persisted events.

- [ ] **Step 5: Commit persistent build events**

```bash
git add api/app/domain/repositories/resource_governance_repository.py api/app/infrastructure/repositories/db_resource_governance_repository.py api/app/application/services/resource_build_service.py api/app/interfaces/endpoints/resource_governance_routes.py api/app/main.py api/tests/app/application/services/test_resource_build_service.py api/tests/app/interfaces/endpoints/test_resource_build_routes.py
git commit -m "feat(resources): persist and replay build events"
```

## Task 12: Complete Shared Governance Verification and Documentation

**Files:**

- Modify: `docs/architecture/checkpoints-and-hitl.zh-CN.md`
- Modify: `docs/architecture/checkpoints-and-hitl.md`
- Modify: `docs/architecture/events.zh-CN.md`
- Modify: `docs/architecture/events.md`
- Modify: `docs/architecture/contract-compatibility.zh-CN.md`
- Modify: `docs/architecture/contract-compatibility.md`
- Create: `api/tests/app/contracts/test_agent_governance_invariants.py`

**Interfaces:**

- Consumes: all interfaces from Tasks 1-11.
- Produces: executable P0 invariant suite and compatibility documentation.

- [ ] **Step 1: Add the end-to-end invariant tests**

```python
@pytest.mark.asyncio
async def test_ask_has_zero_side_effects_across_delegation(governed_runtime):
    result = await governed_runtime.ask(
        "Use a subagent and the ticket integration to create a file and ticket"
    )
    assert result.outcome.status == RunStatus.SUCCEEDED
    assert governed_runtime.side_effects == []
    assert "Agent 模式" in result.last_message


@pytest.mark.asyncio
async def test_mixed_gated_batch_has_zero_preapproval_effects(governed_runtime):
    wait = await governed_runtime.run_until_wait(mixed_side_effect_batch())
    assert wait.approval_batch_id
    assert governed_runtime.side_effects == []


@pytest.mark.asyncio
async def test_every_run_has_exactly_one_terminal_status(governed_runtime):
    for scenario in governed_runtime.failure_scenarios():
        events = await governed_runtime.execute(scenario)
        assert len([e for e in events if e.is_terminal_status]) == 1
```

- [ ] **Step 2: Run the invariant suite before documentation changes**

Run: `cd api && .venv/bin/pytest tests/app/contracts/test_agent_governance_invariants.py -q`

Expected: PASS; if any invariant fails, fix the owning task before continuing.

- [ ] **Step 3: Update architecture docs with the exact new contracts**

Document:

- tool policy fields and conservative default;
- approval batch JSON and resume semantics;
- RunOutcome/event transition table;
- run generation and duplicate dispatch behavior;
- resource build/binding compatibility fields;
- deprecated endpoints and one-release compatibility window.

Do not describe KB/Codebase domain version tables here; those belong to the following plans.

- [ ] **Step 4: Run full shared verification**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/flows tests/app/domain/services/agents tests/app/domain/services/tools tests/app/application/services/test_resource_binding_service.py tests/app/application/services/test_resource_guard_service.py tests/app/application/services/test_resource_build_service.py tests/app/contracts/test_agent_governance_invariants.py -q`

Run: `cd ui && npm test -- --run src/lib/session-events.test.ts src/components/knowledge/knowledge-library.test.tsx`

Run: `cd api && .venv/bin/alembic heads`

Expected: all tests PASS and Alembic prints exactly `b6c7d8e9f0a1 (head)`.

- [ ] **Step 5: Commit shared verification and docs**

```bash
git add docs/architecture/checkpoints-and-hitl.zh-CN.md docs/architecture/checkpoints-and-hitl.md docs/architecture/events.zh-CN.md docs/architecture/events.md docs/architecture/contract-compatibility.zh-CN.md docs/architecture/contract-compatibility.md api/tests/app/contracts/test_agent_governance_invariants.py
git commit -m "docs: document agent and resource governance contracts"
```

## Completion Gate

Do not start the knowledge-base or codebase implementation plans until:

- the Task 12 invariant suite passes;
- Ask has no effectful descriptors at schema or execution time;
- approval batches persist all calls and execute zero side effects before approval;
- non-idempotent retries are proven absent;
- failed/waiting runs cannot become completed;
- migration `b6c7d8e9f0a1` is the sole Alembic head;
- ResourceVersionProvider can be implemented independently by both domain plans.
