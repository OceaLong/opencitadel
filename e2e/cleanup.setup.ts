import { appApi, test } from "./fixtures/acceptance.fixture";
import {
  readBootstrapState,
  writeBootstrapState,
} from "./support/bootstrap-state";
import {
  completeCleanupAction,
  partitionCleanupActions,
  readCleanupActions,
  type CleanupAction,
} from "./support/cleanup-journal";
import { cleanupProductResource } from "./support/product-cleanup";

type ActivePolicy = {
  head: { version: number };
  revision: { id: string };
};

async function executeCleanupAction(
  page: Parameters<typeof appApi>[0],
  value: CleanupAction,
): Promise<void> {
  if (value.action === "restore-runtime-policy") {
    const active = await appApi<ActivePolicy>(
      page,
      `/runtime-policies/${value.policy}`,
    );
    if (active.data.revision.id === value.revision_id) return;
    await appApi(
      page,
      `/runtime-policies/${value.policy}/revisions/${encodeURIComponent(value.revision_id)}/restore`,
      {
        method: "POST",
        body: {
          expected_head_version: active.data.head.version,
          expected_active_revision_id: active.data.revision.id,
          note: `Acceptance cleanup: restore ${value.policy}`,
        },
      },
    );
    return;
  }
  if (value.action === "set-integration-enabled") {
    const collection =
      value.integration === "mcp-server" ? "mcp-servers" : "a2a-servers";
    await appApi(
      page,
      `/integrations/${collection}/${encodeURIComponent(value.resource_id)}/enabled`,
      { method: "PATCH", body: { enabled: value.enabled } },
    );
    return;
  }
  await cleanupProductResource(page, value.resource, value.resource_id, {
    workspaceId: value.workspace_id,
  });
}

test.describe.configure({ mode: "serial" });

test("remove acceptance product resources through public APIs", async ({
  operatorPage: page,
}) => {
  const state = readBootstrapState();
  const errors: string[] = [];
  const phases = partitionCleanupActions(readCleanupActions());

  async function attempt(
    label: string,
    operation: () => Promise<void>,
  ): Promise<void> {
    try {
      await operation();
    } catch (error) {
      errors.push(
        `${label}: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  for (const entry of phases.resources) {
    await attempt(`cleanup journal ${entry.order}`, async () => {
      await executeCleanupAction(page, entry.value);
      completeCleanupAction(entry);
    });
  }

  if (state && !state.cleanup_completed) {
    for (const purpose of [...state.binding_purposes].reverse()) {
      await attempt(`delete inference binding ${purpose}`, async () => {
        await appApi(
          page,
          `/inference/bindings/${purpose}?binding_scope=global`,
          { method: "DELETE", expectStatus: [200, 404] },
        );
      });
    }
    for (const modelId of [state.model_ids.embedding, state.model_ids.chat]) {
      if (!modelId) continue;
      await attempt(`delete inference model ${modelId}`, async () => {
        await appApi(page, `/inference/models/${encodeURIComponent(modelId)}`, {
          method: "DELETE",
          expectStatus: [200, 404],
        });
      });
    }
    if (state.endpoint_id) {
      await attempt(
        `delete inference endpoint ${state.endpoint_id}`,
        async () => {
          await appApi(
            page,
            `/inference/endpoints/${encodeURIComponent(state.endpoint_id as string)}`,
            { method: "DELETE", expectStatus: [200, 404] },
          );
        },
      );
    }
  }

  for (const entry of phases.state) {
    await attempt(`cleanup journal ${entry.order}`, async () => {
      await executeCleanupAction(page, entry.value);
      completeCleanupAction(entry);
    });
  }

  if (state && !state.cleanup_completed && errors.length === 0) {
    state.cleanup_completed = true;
    writeBootstrapState(state);
  }
  if (errors.length > 0) {
    throw new Error(`acceptance cleanup failures:\n${errors.join("\n")}`);
  }
});
