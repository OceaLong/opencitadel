import { describe, expect, it } from "vitest";

import { emptyModelInput, providerSupportsKind } from "./use-inference-settings";

describe("inference settings constraints", () => {
  it("only offers embedding models for providers with an embedding adapter", () => {
    expect(providerSupportsKind("openai", "embedding")).toBe(true);
    expect(providerSupportsKind("azure", "embedding")).toBe(true);
    expect(providerSupportsKind("ollama", "embedding")).toBe(true);
    expect(providerSupportsKind("anthropic", "embedding")).toBe(false);
    expect(providerSupportsKind("gemini", "embedding")).toBe(false);
    expect(providerSupportsKind("anthropic", "chat")).toBe(true);
  });

  it("creates a typed chat model by default", () => {
    const input = emptyModelInput("endpoint-1");

    expect(input.endpoint_id).toBe("endpoint-1");
    expect(input.kind).toBe("chat");
    expect(input.settings).toEqual({
      kind: "chat",
      temperature: 0.7,
      max_output_tokens: 8192,
    });
  });
});
