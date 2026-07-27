import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./fetch", () => ({
  del: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

import { post, put } from "./fetch";
import { modelsApi } from "./models";

describe("modelsApi default control", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("omits is_default from generic create requests", async () => {
    vi.mocked(post).mockResolvedValue({} as never);

    await modelsApi.create({
      endpoint_id: "endpoint-1",
      display_name: "Model",
      model_name: "gpt-test",
      is_default: true,
    });

    expect(post).toHaveBeenCalledWith("/llm-models", {
      endpoint_id: "endpoint-1",
      display_name: "Model",
      model_name: "gpt-test",
    });
  });

  it("omits is_default from generic update requests", async () => {
    vi.mocked(put).mockResolvedValue({} as never);

    await modelsApi.update("model-1", {
      display_name: "Changed",
      is_default: false,
    });

    expect(put).toHaveBeenCalledWith("/llm-models/model-1", {
      display_name: "Changed",
    });
  });

  it("uses a dedicated workspace preference endpoint", async () => {
    vi.mocked(post).mockResolvedValue({} as never);

    await modelsApi.setPreferred("model-1");

    expect(post).toHaveBeenCalledWith(
      "/llm-models/model-1/set-preferred",
      {},
    );
  });
});
