// @vitest-environment jsdom

import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { InferenceBinding, InferenceEndpoint, InferenceModel } from "@/lib/api/inference";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";
import zh from "../../../messages/zh.json";

const mocks = vi.hoisted(() => ({ useInferenceSettings: vi.fn() }));

vi.mock("@/hooks/use-inference-settings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/use-inference-settings")>()),
  useInferenceSettings: mocks.useInferenceSettings,
}));

import { InferenceSettings } from "./inference-settings";

afterEach(() => {
  document.body.replaceChildren();
});

function stateFixture() {
  const endpoint = {
    id: "endpoint-1",
    display_name: "Primary OpenAI",
    provider: "openai",
    base_url: "https://api.openai.com/v1",
    credential_configured: true,
    visibility: "private",
    owner_user_id: "user-1",
  } as InferenceEndpoint;
  const chat = {
    id: "chat-1",
    endpoint_id: endpoint.id,
    display_name: "Primary Chat",
    model_name: "gpt-chat",
    kind: "chat",
    settings: { kind: "chat", temperature: 0.7, max_output_tokens: 8192 },
    visibility: "private",
    owner_user_id: "user-1",
  } as InferenceModel;
  const embedding = {
    id: "embedding-1",
    endpoint_id: endpoint.id,
    display_name: "Primary Embedding",
    model_name: "text-embedding",
    kind: "embedding",
    settings: { kind: "embedding", dimensions: 1536, max_batch_size: 64 },
    visibility: "private",
    owner_user_id: "user-1",
  } as InferenceModel;
  const bindings = [
    { purpose: "chat", model_id: chat.id, owner_user_id: "user-1" },
    { purpose: "embedding", model_id: embedding.id, owner_user_id: "user-1" },
  ] as InferenceBinding[];

  return {
    endpoints: [endpoint],
    models: [chat, embedding],
    bindings,
    loading: false,
    saving: false,
    probingId: null,
    endpointDialogOpen: false,
    setEndpointDialogOpen: vi.fn(),
    modelDialogOpen: false,
    setModelDialogOpen: vi.fn(),
    editingEndpoint: null,
    editingModel: null,
    endpointInput: {
      display_name: "",
      provider: "openai",
      base_url: "",
      credential: "",
      visibility: "private",
    },
    setEndpointInput: vi.fn(),
    modelInput: chat,
    setModelInput: vi.fn(),
    openEndpointCreate: vi.fn(),
    openEndpointEdit: vi.fn(),
    saveEndpoint: vi.fn(),
    deleteEndpoint: vi.fn(),
    openModelCreate: vi.fn(),
    openModelEdit: vi.fn(),
    saveModel: vi.fn(),
    deleteModel: vi.fn(),
    probeModel: vi.fn(),
    setBinding: vi.fn(),
    deleteBinding: vi.fn(),
    reload: vi.fn(),
  };
}

describe.each([
  ["en", en],
  ["zh", zh],
] as const)("Inference settings %s", (locale, messages) => {
  it("renders typed models and all three purpose bindings", async () => {
    mocks.useInferenceSettings.mockReturnValue(stateFixture());

    const { container, unmount } = await renderComponent(
      <NextIntlClientProvider locale={locale} messages={messages}>
        <InferenceSettings userId="user-1" />
      </NextIntlClientProvider>,
    );

    expect(container.textContent).toContain(messages.settingsInference.inferenceTitle);
    expect(container.textContent).toContain(messages.settingsInference.purpose_chat);
    expect(container.textContent).toContain(messages.settingsInference.purpose_embedding);
    expect(container.textContent).toContain(messages.settingsInference.purpose_rerank);
    expect(container.textContent).toContain("Primary Chat");
    expect(container.textContent).toContain("Primary Embedding");
    expect(container.textContent).toContain("1536");
    await unmount();
  });
});
