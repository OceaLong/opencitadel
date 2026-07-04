import { describe, expect, it } from "vitest";

import { modelErrorMessage } from "./llm-status";

describe("modelErrorMessage", () => {
  it("returns quota-specific message for MODEL_QUOTA_EXCEEDED", () => {
    const message = modelErrorMessage("MODEL_QUOTA_EXCEEDED");
    expect(message).toBeTruthy();
    expect(message).not.toContain("insufficient_quota");
  });

  it("returns generic model message for other MODEL_ codes", () => {
    const message = modelErrorMessage("MODEL_UNAVAILABLE");
    expect(message).toBeTruthy();
    expect(message).not.toBe(modelErrorMessage("MODEL_QUOTA_EXCEEDED"));
  });

  it("returns invalid-request message for MODEL_INVALID_REQUEST", () => {
    const message = modelErrorMessage("MODEL_INVALID_REQUEST");
    expect(message).toBeTruthy();
    expect(message).not.toBe(modelErrorMessage("MODEL_UNAVAILABLE"));
    expect(message).not.toBe(modelErrorMessage("MODEL_QUOTA_EXCEEDED"));
  });

  it("returns infra message for TASK_INFRA_FAILED", () => {
    const message = modelErrorMessage("TASK_INFRA_FAILED");
    expect(message).toBeTruthy();
    expect(message).not.toBe(modelErrorMessage("MODEL_UNAVAILABLE"));
  });

  it("returns null for unknown codes", () => {
    expect(modelErrorMessage("UNKNOWN")).toBeNull();
    expect(modelErrorMessage(null)).toBeNull();
  });
});
