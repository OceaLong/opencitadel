import type { Page } from "@playwright/test";

import type { components } from "../ui/src/lib/api/generated/schema";
import { appApi, expect, test } from "./fixtures/acceptance.fixture";
import type {
  BootstrapCapabilityName,
  BootstrapCapabilityState,
} from "./support/bootstrap-state";
import {
  completeCleanupAction,
  registerCleanupAction,
} from "./support/cleanup-journal";
import { acceptanceId } from "./support/ids";

type Endpoint = components["schemas"]["InferenceEndpointResponse"];
type EndpointList = components["schemas"]["InferenceEndpointListResponse"];
type Model = components["schemas"]["InferenceModelResponse"];
type ModelList = components["schemas"]["InferenceModelListResponse"];
type Binding = components["schemas"]["InferenceBindingResponse"];
type BindingList = components["schemas"]["InferenceBindingListResponse"];
type Probe = components["schemas"]["InferenceProbeResponse"];
type ActiveExecution = components["schemas"]["ActiveExecutionPolicyResponse"];
type ActiveOperations = components["schemas"]["ActiveOperationsPolicyResponse"];
type ExecutionHistory =
  components["schemas"]["ExecutionPolicyRevisionListResponse"];
type OperationsHistory =
  components["schemas"]["OperationsPolicyRevisionListResponse"];

type InferenceStatus = {
  capabilities: { items: Record<string, BootstrapCapabilityState> };
};
type CapabilityTransition = {
  before: Record<BootstrapCapabilityName, BootstrapCapabilityState>;
  after: Record<BootstrapCapabilityName, BootstrapCapabilityState>;
};
type Team = { id: string; name: string; description: string };

function cover(...requirementIds: string[]): void {
  for (const requirementId of requirementIds) {
    test
      .info()
      .annotations.push({ type: "acceptance", description: requirementId });
  }
}

function requireBootstrapIds(state: {
  endpoint_id?: string;
  model_ids: { chat?: string; embedding?: string };
}): { endpointId: string; chatId: string; embeddingId: string } {
  if (
    !state.endpoint_id ||
    !state.model_ids.chat ||
    !state.model_ids.embedding
  ) {
    throw new Error("bootstrap inference endpoint and model IDs are required");
  }
  return {
    endpointId: state.endpoint_id,
    chatId: state.model_ids.chat,
    embeddingId: state.model_ids.embedding,
  };
}

async function createOwnedTeam(page: Page, suffix: string): Promise<Team> {
  const team = (
    await appApi<Team>(page, "/teams", {
      method: "POST",
      body: {
        name: acceptanceId(suffix),
        description: "Acceptance-only control-plane workspace",
      },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "team",
    resource_id: team.id,
  });
  return team;
}

async function useWorkspace(page: Page, teamId: string): Promise<void> {
  await page.evaluate((id) => {
    window.localStorage.setItem("opencitadel-active-workspace", id);
  }, teamId);
}

function nextBounded(value: number, minimum: number, maximum: number): number {
  return value < maximum ? value + 1 : Math.max(minimum, value - 1);
}

test.describe("inference control plane", () => {
  test("endpoint reads mask credentials at every public projection", async ({
    operatorPage: page,
    bootstrapState,
  }) => {
    cover("INF-ENDPOINT");
    const { endpointId } = requireBootstrapIds(bootstrapState);

    const endpoint = await appApi<Endpoint>(
      page,
      `/inference/endpoints/${encodeURIComponent(endpointId)}`,
    );
    const endpoints = await appApi<EndpointList>(page, "/inference/endpoints");
    const listed = endpoints.data.items?.find((item) => item.id === endpointId);

    expect(endpoint.data).toMatchObject({
      id: endpointId,
      provider: "openai",
      base_url: "http://acceptance-inference:8080/v1",
      credential_configured: true,
      visibility: "global",
    });
    expect(listed).toEqual(endpoint.data);
    expect(endpoint.data).not.toHaveProperty("credential");
    expect(listed).not.toHaveProperty("credential");
  });

  test("chat and embedding model projections retain their typed settings", async ({
    operatorPage: page,
    bootstrapState,
  }) => {
    cover("INF-MODEL");
    const { endpointId, chatId, embeddingId } =
      requireBootstrapIds(bootstrapState);

    const models = await appApi<ModelList>(page, "/inference/models");
    const chat = models.data.items?.find((item) => item.id === chatId);
    const embedding = models.data.items?.find(
      (item) => item.id === embeddingId,
    );

    expect(chat).toMatchObject({
      id: chatId,
      endpoint_id: endpointId,
      model_name: "acceptance-chat",
      kind: "chat",
      settings: { kind: "chat", temperature: 0, max_output_tokens: 4096 },
      visibility: "global",
    });
    expect(embedding).toMatchObject({
      id: embeddingId,
      endpoint_id: endpointId,
      model_name: "acceptance-embedding-1536",
      kind: "embedding",
      settings: {
        kind: "embedding",
        dimensions: 1536,
        max_batch_size: 32,
      },
      visibility: "global",
    });
  });

  test("chat and embedding probes traverse the configured provider", async ({
    operatorPage: page,
    bootstrapState,
  }) => {
    cover("INF-PROBE");
    const { chatId, embeddingId } = requireBootstrapIds(bootstrapState);

    for (const modelId of [chatId, embeddingId]) {
      const probe = await appApi<Probe>(
        page,
        `/inference/models/${encodeURIComponent(modelId)}/probe`,
        { method: "POST" },
      );
      expect(probe.data).toMatchObject({ status: "ok", error_key: null });
    }
  });

  test("workspace binding overrides and removal preserve global inheritance", async ({
    operatorPage: page,
    bootstrapState,
  }) => {
    cover("INF-BIND");
    const { chatId } = requireBootstrapIds(bootstrapState);
    const team = await createOwnedTeam(page, "inference-binding-team");
    await useWorkspace(page, team.id);

    const inherited = await appApi<BindingList>(page, "/inference/bindings");
    expect(
      inherited.data.items?.find((item) => item.purpose === "chat"),
    ).toMatchObject({ model_id: chatId, team_id: null, owner_user_id: null });

    const override = await appApi<Binding>(page, "/inference/bindings/chat", {
      method: "PUT",
      body: { model_id: chatId, binding_scope: "workspace" },
    });
    expect(override.data).toMatchObject({
      purpose: "chat",
      model_id: chatId,
      team_id: team.id,
      owner_user_id: null,
    });

    const overridden = await appApi<BindingList>(page, "/inference/bindings");
    expect(
      overridden.data.items?.find((item) => item.purpose === "chat"),
    ).toMatchObject({ model_id: chatId, team_id: team.id });

    await appApi(page, "/inference/bindings/chat?binding_scope=workspace", {
      method: "DELETE",
    });
    const restored = await appApi<BindingList>(page, "/inference/bindings");
    expect(
      restored.data.items?.find((item) => item.purpose === "chat"),
    ).toMatchObject({ model_id: chatId, team_id: null, owner_user_id: null });
  });

  test("bootstrap proves capabilities transition from unconfigured to available", async ({
    operatorPage: page,
    bootstrapState,
  }) => {
    cover("INF-CAP");
    const { chatId, embeddingId } = requireBootstrapIds(bootstrapState);
    const transition = (
      bootstrapState as typeof bootstrapState & {
        capability_transition?: CapabilityTransition;
      }
    ).capability_transition;

    expect(
      transition,
      "bootstrap must retain both capability projections",
    ).toBeDefined();
    expect(transition?.before.chat.state).toBe("not_configured");
    expect(transition?.before.embeddings.state).toBe("not_configured");
    expect(transition?.before.rerank.state).toBe("not_configured");
    expect(transition?.after.chat).toMatchObject({
      state: "available",
      model_id: chatId,
    });
    expect(transition?.after.embeddings).toMatchObject({
      state: "available",
      model_id: embeddingId,
    });
    expect(transition?.after.rerank).toMatchObject({
      state: "available",
      model_id: chatId,
    });

    const current = await appApi<InferenceStatus>(page, "/inference/status");
    expect(current.data.capabilities.items.chat).toMatchObject(
      transition?.after.chat ?? {},
    );
    expect(current.data.capabilities.items.embeddings).toMatchObject(
      transition?.after.embeddings ?? {},
    );
    expect(current.data.capabilities.items.rerank).toMatchObject(
      transition?.after.rerank ?? {},
    );
  });

  test("incompatible workspace binding is visibly rejected without changing inheritance", async ({
    operatorPage: page,
    bootstrapState,
  }) => {
    cover("INF-MISMATCH");
    const { chatId, embeddingId } = requireBootstrapIds(bootstrapState);
    const team = await createOwnedTeam(page, "inference-mismatch-team");
    await useWorkspace(page, team.id);

    const rejected = await appApi<Record<string, never>>(
      page,
      "/inference/bindings/chat",
      {
        method: "PUT",
        body: { model_id: embeddingId, binding_scope: "workspace" },
        expectStatus: 400,
      },
    );
    expect(rejected.errorKey).toBe("inference.errors.bindingKindMismatch");
    expect(rejected.errorParams).toEqual({
      purpose: "chat",
      kind: "embedding",
    });
    expect(rejected.msg).toBeTruthy();

    const bindings = await appApi<BindingList>(page, "/inference/bindings");
    expect(
      bindings.data.items?.find((item) => item.purpose === "chat"),
    ).toMatchObject({ model_id: chatId, team_id: null, owner_user_id: null });
  });
});

test.describe("Runtime Policy control plane", () => {
  test.describe.configure({ mode: "serial" });

  test("edits, conflicts, history, and restore remain CAS-safe and append-only", async ({
    operatorPage: page,
  }) => {
    cover("POL-EDIT", "POL-HISTORY", "POL-CAS", "POL-RESTORE");

    const originalExecution = (
      await appApi<ActiveExecution>(page, "/runtime-policies/execution")
    ).data;
    const originalOperations = (
      await appApi<ActiveOperations>(page, "/runtime-policies/operations")
    ).data;
    const executionCleanup = registerCleanupAction({
      action: "restore-runtime-policy",
      policy: "execution",
      revision_id: originalExecution.revision.id,
    });
    const operationsCleanup = registerCleanupAction({
      action: "restore-runtime-policy",
      policy: "operations",
      revision_id: originalOperations.revision.id,
    });

    const originalAgent = originalExecution.revision.policy.agent;
    const originalScheduler = originalOperations.revision.policy.scheduler;
    if (!originalAgent || !originalScheduler) {
      throw new Error("active Runtime Policy is missing required sections");
    }

    const editedExecution = (
      await appApi<ActiveExecution>(
        page,
        "/runtime-policies/execution/revisions",
        {
          method: "POST",
          body: {
            expected_head_version: originalExecution.head.version,
            expected_active_revision_id: originalExecution.revision.id,
            policy: {
              ...originalExecution.revision.policy,
              agent: {
                ...originalAgent,
                max_iterations: nextBounded(
                  originalAgent.max_iterations,
                  1,
                  100,
                ),
              },
            },
            note: "Acceptance control plane: edit execution",
          },
        },
      )
    ).data;
    expect(editedExecution.revision.id).not.toBe(originalExecution.revision.id);
    expect(editedExecution.revision.sequence).toBeGreaterThan(
      originalExecution.revision.sequence,
    );
    expect(editedExecution.head.version).toBeGreaterThan(
      originalExecution.head.version,
    );

    const operationsBeforeEdit = (
      await appApi<ActiveOperations>(page, "/runtime-policies/operations")
    ).data;
    const editedOperations = (
      await appApi<ActiveOperations>(
        page,
        "/runtime-policies/operations/revisions",
        {
          method: "POST",
          body: {
            expected_head_version: operationsBeforeEdit.head.version,
            expected_active_revision_id: operationsBeforeEdit.revision.id,
            policy: {
              ...operationsBeforeEdit.revision.policy,
              scheduler: {
                ...originalScheduler,
                poll_interval_seconds: nextBounded(
                  originalScheduler.poll_interval_seconds,
                  0.1,
                  3_600,
                ),
              },
            },
            note: "Acceptance control plane: edit operations",
          },
        },
      )
    ).data;
    expect(editedOperations.revision.id).not.toBe(
      originalOperations.revision.id,
    );
    expect(editedOperations.revision.sequence).toBeGreaterThan(
      originalOperations.revision.sequence,
    );

    await page
      .getByRole("button", {
        name: /Open inference settings|打开推理设置/,
      })
      .click();
    await page
      .getByRole("button", { name: /Runtime policy|运行时策略/ })
      .click();
    await expect(
      page.getByRole("tab", { name: /Execution policy|执行策略/ }),
    ).toBeVisible();
    await expect(
      page.getByText("Acceptance control plane: edit execution"),
    ).toBeVisible();

    const maxIterations = page.getByLabel(/Maximum iterations|最大迭代次数/);
    const activeAgent = editedExecution.revision.policy.agent;
    if (!activeAgent)
      throw new Error("edited Execution Policy has no agent section");
    const preservedDraftValue = nextBounded(activeAgent.max_iterations, 1, 100);
    await maxIterations.fill(String(preservedDraftValue));
    const semanticDiff = page.getByLabel(
      /Pending policy changes|待生效策略变更/,
    );
    await expect(semanticDiff).toContainText("agent.max_iterations");
    await page
      .getByLabel(/Change note|变更说明/)
      .fill("Acceptance control plane: stale UI draft");

    const executionBeforeConcurrent = (
      await appApi<ActiveExecution>(page, "/runtime-policies/execution")
    ).data;
    const concurrentAgent = executionBeforeConcurrent.revision.policy.agent;
    if (!concurrentAgent) {
      throw new Error("concurrent Execution Policy has no agent section");
    }
    const concurrentExecution = (
      await appApi<ActiveExecution>(
        page,
        "/runtime-policies/execution/revisions",
        {
          method: "POST",
          body: {
            expected_head_version: executionBeforeConcurrent.head.version,
            expected_active_revision_id: executionBeforeConcurrent.revision.id,
            policy: {
              ...executionBeforeConcurrent.revision.policy,
              agent: {
                ...concurrentAgent,
                max_retries: nextBounded(concurrentAgent.max_retries, 0, 10),
              },
            },
            note: "Acceptance control plane: concurrent execution",
          },
        },
      )
    ).data;

    await page
      .getByRole("button", {
        name: /Save execution policy|保存执行策略/,
      })
      .click();
    await expect(
      page.getByText(
        /The active policy changed while you were editing|编辑期间活动策略已变化/,
      ),
    ).toBeVisible();
    await expect(maxIterations).toHaveValue(String(preservedDraftValue));

    const stale = await appApi<
      components["schemas"]["RuntimePolicyHeadResponse"]
    >(page, "/runtime-policies/execution/revisions", {
      method: "POST",
      body: {
        expected_head_version: editedExecution.head.version,
        expected_active_revision_id: editedExecution.revision.id,
        policy: editedExecution.revision.policy,
        note: "Acceptance control plane: explicit stale edit",
      },
      expectStatus: 409,
    });
    expect(stale.errorKey).toBe("runtimePolicy.headConflict");
    expect(stale.data.version).toBe(concurrentExecution.head.version);
    expect(stale.data.execution_revision_id).toBe(
      concurrentExecution.revision.id,
    );
    const activeAfterConflict = (
      await appApi<ActiveExecution>(page, "/runtime-policies/execution")
    ).data;
    expect(activeAfterConflict.revision.id).toBe(
      concurrentExecution.revision.id,
    );

    const executionHistory = (
      await appApi<ExecutionHistory>(
        page,
        "/runtime-policies/execution/revisions?limit=20&offset=0",
      )
    ).data;
    const operationsHistory = (
      await appApi<OperationsHistory>(
        page,
        "/runtime-policies/operations/revisions?limit=20&offset=0",
      )
    ).data;
    expect(executionHistory.items.map((revision) => revision.id)).toEqual(
      expect.arrayContaining([
        originalExecution.revision.id,
        editedExecution.revision.id,
        concurrentExecution.revision.id,
      ]),
    );
    expect(operationsHistory.items.map((revision) => revision.id)).toEqual(
      expect.arrayContaining([
        originalOperations.revision.id,
        editedOperations.revision.id,
      ]),
    );

    const executionBeforeRestore = (
      await appApi<ActiveExecution>(page, "/runtime-policies/execution")
    ).data;
    const restoredExecution = (
      await appApi<ActiveExecution>(
        page,
        `/runtime-policies/execution/revisions/${encodeURIComponent(originalExecution.revision.id)}/restore`,
        {
          method: "POST",
          body: {
            expected_head_version: executionBeforeRestore.head.version,
            expected_active_revision_id: executionBeforeRestore.revision.id,
            note: "Acceptance control plane: restore execution",
          },
        },
      )
    ).data;
    expect(restoredExecution.revision.id).not.toBe(
      originalExecution.revision.id,
    );
    expect(restoredExecution.revision.restored_from_id).toBe(
      originalExecution.revision.id,
    );
    expect(restoredExecution.revision.sequence).toBeGreaterThan(
      concurrentExecution.revision.sequence,
    );
    expect(restoredExecution.revision.policy).toEqual(
      originalExecution.revision.policy,
    );
    completeCleanupAction(executionCleanup);

    const operationsBeforeRestore = (
      await appApi<ActiveOperations>(page, "/runtime-policies/operations")
    ).data;
    const restoredOperations = (
      await appApi<ActiveOperations>(
        page,
        `/runtime-policies/operations/revisions/${encodeURIComponent(originalOperations.revision.id)}/restore`,
        {
          method: "POST",
          body: {
            expected_head_version: operationsBeforeRestore.head.version,
            expected_active_revision_id: operationsBeforeRestore.revision.id,
            note: "Acceptance control plane: restore operations",
          },
        },
      )
    ).data;
    expect(restoredOperations.revision.id).not.toBe(
      originalOperations.revision.id,
    );
    expect(restoredOperations.revision.restored_from_id).toBe(
      originalOperations.revision.id,
    );
    expect(restoredOperations.revision.sequence).toBeGreaterThan(
      editedOperations.revision.sequence,
    );
    expect(restoredOperations.revision.policy).toEqual(
      originalOperations.revision.policy,
    );
    completeCleanupAction(operationsCleanup);

    const finalExecutionHistory = (
      await appApi<ExecutionHistory>(
        page,
        "/runtime-policies/execution/revisions?limit=20&offset=0",
      )
    ).data;
    const finalOperationsHistory = (
      await appApi<OperationsHistory>(
        page,
        "/runtime-policies/operations/revisions?limit=20&offset=0",
      )
    ).data;
    expect(
      finalExecutionHistory.items.find(
        (revision) => revision.id === restoredExecution.revision.id,
      )?.restored_from_id,
    ).toBe(originalExecution.revision.id);
    expect(
      finalOperationsHistory.items.find(
        (revision) => revision.id === restoredOperations.revision.id,
      )?.restored_from_id,
    ).toBe(originalOperations.revision.id);
  });
});
