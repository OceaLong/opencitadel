import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { readLocaleCookie } from "./detect-locale";
import { translate } from "./translate";

describe("translate locale detection", () => {
  beforeEach(() => {
    vi.stubGlobal("document", {
      cookie: "",
    });
    vi.stubGlobal("navigator", {
      language: "en-US",
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses explicit locale when provided", () => {
    const zh = translate("sessionDetail.modelFallbackNotice", { modelName: "qwen" }, "zh");
    const en = translate("sessionDetail.modelFallbackNotice", { modelName: "qwen" }, "en");

    expect(zh).toContain("qwen");
    expect(zh).toContain("配额");
    expect(en).toContain("qwen");
    expect(en).toContain("quota");
  });

  it("prefers NEXT_LOCALE cookie over navigator.language", () => {
    document.cookie = "NEXT_LOCALE=zh";
    Object.defineProperty(navigator, "language", { value: "en-US", configurable: true });

    const message = translate("sessionDetail.modelFallbackNotice", { modelName: "qwen" });

    expect(message).toContain("配额");
  });

  it("falls back to navigator.language when cookie is missing", () => {
    document.cookie = "";
    Object.defineProperty(navigator, "language", { value: "zh-CN", configurable: true });

    const message = translate("sessionDetail.modelFallbackNotice", { modelName: "qwen" });

    expect(message).toContain("配额");
    expect(message).not.toContain("quota is exhausted");
  });

  it("readLocaleCookie returns null when cookie is absent", () => {
    document.cookie = "";
    expect(readLocaleCookie()).toBeNull();
  });
});
