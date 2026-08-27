import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./fetch", () => ({
  del: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

import { del, get, post, put } from "./fetch";
import { inferenceApi } from "./inference";

describe("inferenceApi", () => {
  beforeEach(() => vi.clearAllMocks());

  it("uses the stable endpoint and model paths", async () => {
    vi.mocked(get).mockResolvedValue({ items: [] } as never);
    vi.mocked(post).mockResolvedValue({} as never);
    vi.mocked(put).mockResolvedValue({} as never);

    await inferenceApi.listEndpoints();
    await inferenceApi.listModels();
    await inferenceApi.probeModel("model-1");

    expect(get).toHaveBeenCalledWith("/inference/endpoints");
    expect(get).toHaveBeenCalledWith("/inference/models");
    expect(post).toHaveBeenCalledWith("/inference/models/model-1/probe", {});
  });

  it("uses purpose bindings instead of default/preference actions", async () => {
    vi.mocked(put).mockResolvedValue({} as never);
    vi.mocked(del).mockResolvedValue(undefined as never);

    await inferenceApi.setBinding("chat", {
      model_id: "model-1",
      binding_scope: "workspace",
    });
    await inferenceApi.deleteBinding("embedding", "global");

    expect(put).toHaveBeenCalledWith("/inference/bindings/chat", {
      model_id: "model-1",
      binding_scope: "workspace",
    });
    expect(del).toHaveBeenCalledWith("/inference/bindings/embedding?binding_scope=global");
  });
});
