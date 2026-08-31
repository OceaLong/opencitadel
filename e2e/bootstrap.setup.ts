import { appApi, expect, test } from "./fixtures/acceptance.fixture";
import type { components } from "../ui/src/lib/api/generated/schema";
import {
  type BootstrapCapabilityProjection,
  type BootstrapState,
  writeBootstrapState,
} from "./support/bootstrap-state";
import { registerCleanupAction } from "./support/cleanup-journal";
import { acceptanceId } from "./support/ids";
import { pollProjection } from "./support/poll";

type Endpoint = {
  id: string;
  display_name: string;
  credential_configured: boolean;
};

type Model = {
  id: string;
  model_name: string;
  kind: "chat" | "embedding";
};

type Probe = {
  status: "ok" | "error";
  error_key: string | null;
};

type InferenceStatus = {
  capabilities: {
    items: Record<
      string,
      { state: string; model_id: string | null; reason_key: string | null }
    >;
  };
};

function capabilityProjection(
  status: InferenceStatus,
): BootstrapCapabilityProjection {
  const { items } = status.capabilities;
  const projection = {
    chat: items.chat,
    embeddings: items.embeddings,
    rerank: items.rerank,
  };
  for (const [name, value] of Object.entries(projection)) {
    if (!value) {
      throw new Error(`inference status is missing ${name} capability`);
    }
  }
  return projection as BootstrapCapabilityProjection;
}

type ActiveOperationsPolicy =
  components["schemas"]["ActiveOperationsPolicyResponse"];

test.describe.configure({ mode: "serial" });

test("bootstrap deterministic inference through the public control plane", async ({
  operatorPage: page,
}) => {
  const state: BootstrapState = {
    schema_version: 1,
    run_id: process.env.ACCEPTANCE_RUN_ID as string,
    model_ids: {},
    binding_purposes: [],
    cleanup_completed: false,
  };
  writeBootstrapState(state);

  const beforeCapabilities = capabilityProjection(
    (await appApi<InferenceStatus>(page, "/inference/status")).data,
  );
  for (const [name, capability] of Object.entries(beforeCapabilities)) {
    expect(
      capability.state,
      `${name} capability must begin unconfigured in a disposable run`,
    ).toBe("not_configured");
  }

  const operations = await appApi<ActiveOperationsPolicy>(
    page,
    "/runtime-policies/operations",
  );
  const traffic = operations.data.revision.policy.traffic;
  if (!traffic) {
    throw new Error("active Operations Policy is missing its traffic contract");
  }
  if (traffic.requests_per_minute !== 100_000) {
    registerCleanupAction({
      action: "restore-runtime-policy",
      policy: "operations",
      revision_id: operations.data.revision.id,
    });
    await appApi<ActiveOperationsPolicy>(
      page,
      "/runtime-policies/operations/revisions",
      {
        method: "POST",
        body: {
          expected_head_version: operations.data.head.version,
          expected_active_revision_id: operations.data.revision.id,
          policy: {
            ...operations.data.revision.policy,
            traffic: {
              ...traffic,
              rate_limit_enabled: true,
              requests_per_minute: 100_000,
            },
          },
          note: "Acceptance bootstrap: deterministic request budget",
        },
      },
    );
  }

  const endpoint = await appApi<Endpoint>(page, "/inference/endpoints", {
    method: "POST",
    body: {
      display_name: acceptanceId("endpoint"),
      provider: "openai",
      base_url: "http://acceptance-inference:8080/v1",
      credential: process.env.ACCEPTANCE_PROVIDER_TOKEN,
      visibility: "global",
    },
  });
  state.endpoint_id = endpoint.data.id;
  writeBootstrapState(state);

  const endpointRead = await appApi<Endpoint>(
    page,
    `/inference/endpoints/${encodeURIComponent(endpoint.data.id)}`,
  );
  expect(endpointRead.data.credential_configured).toBe(true);
  expect("credential" in endpointRead.data).toBe(false);

  const chat = await appApi<Model>(page, "/inference/models", {
    method: "POST",
    body: {
      endpoint_id: endpoint.data.id,
      display_name: acceptanceId("chat"),
      model_name: "acceptance-chat",
      kind: "chat",
      settings: { kind: "chat", temperature: 0, max_output_tokens: 4096 },
      input_price_per_million: 0,
      output_price_per_million: 0,
      extra_params: {},
      capabilities: {},
      visibility: "global",
    },
  });
  state.model_ids.chat = chat.data.id;
  writeBootstrapState(state);

  const embedding = await appApi<Model>(page, "/inference/models", {
    method: "POST",
    body: {
      endpoint_id: endpoint.data.id,
      display_name: acceptanceId("embedding"),
      model_name: "acceptance-embedding-1536",
      kind: "embedding",
      settings: { kind: "embedding", dimensions: 1536, max_batch_size: 32 },
      input_price_per_million: 0,
      output_price_per_million: 0,
      extra_params: {},
      capabilities: {},
      visibility: "global",
    },
  });
  state.model_ids.embedding = embedding.data.id;
  writeBootstrapState(state);

  for (const modelId of [chat.data.id, embedding.data.id]) {
    const probe = await appApi<Probe>(
      page,
      `/inference/models/${encodeURIComponent(modelId)}/probe`,
      { method: "POST" },
    );
    expect(probe.data).toMatchObject({ status: "ok", error_key: null });
  }

  const bindings = [
    ["chat", chat.data.id],
    ["embedding", embedding.data.id],
    ["rerank", chat.data.id],
  ] as const;
  for (const [purpose, modelId] of bindings) {
    await appApi(page, `/inference/bindings/${purpose}`, {
      method: "PUT",
      body: { model_id: modelId, binding_scope: "global" },
    });
    state.binding_purposes.push(purpose);
    writeBootstrapState(state);
  }

  const status = await pollProjection(
    async () => (await appApi<InferenceStatus>(page, "/inference/status")).data,
    (projection) =>
      projection.capabilities.items.chat?.state === "available" &&
      projection.capabilities.items.chat?.model_id === chat.data.id &&
      projection.capabilities.items.embeddings?.state === "available" &&
      projection.capabilities.items.embeddings?.model_id ===
        embedding.data.id &&
      projection.capabilities.items.rerank?.state === "available" &&
      projection.capabilities.items.rerank?.model_id === chat.data.id,
    { message: "inference capabilities become available" },
  );
  expect(status.capabilities.items.chat.state).toBe("available");
  state.capability_transition = {
    before: beforeCapabilities,
    after: capabilityProjection(status),
  };
  writeBootstrapState(state);
});
