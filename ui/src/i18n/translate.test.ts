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
    const zh = translate("common.pageOf", { current: 2, total: 5 }, "zh");
    const en = translate("common.pageOf", { current: 2, total: 5 }, "en");

    expect(zh).toContain("第 2 / 5 页");
    expect(en).toContain("Page 2 of 5");
  });

  it("prefers NEXT_LOCALE cookie over navigator.language", () => {
    document.cookie = "NEXT_LOCALE=zh";
    Object.defineProperty(navigator, "language", { value: "en-US", configurable: true });

    const message = translate("common.pageOf", { current: 2, total: 5 });

    expect(message).toContain("第 2 / 5 页");
  });

  it("falls back to navigator.language when cookie is missing", () => {
    document.cookie = "";
    Object.defineProperty(navigator, "language", { value: "zh-CN", configurable: true });

    const message = translate("common.pageOf", { current: 2, total: 5 });

    expect(message).toContain("第 2 / 5 页");
    expect(message).not.toContain("Page 2 of 5");
  });

  it("readLocaleCookie returns null when cookie is absent", () => {
    document.cookie = "";
    expect(readLocaleCookie()).toBeNull();
  });
});
