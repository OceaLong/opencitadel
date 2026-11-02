import { expect, test } from "@playwright/test";
import {
  selectHistoryCuts,
  selectAssistantMessageForStep,
  type HistoricalView,
} from "../support/execution-history";

test("final message follows its model step even when earlier tool-request messages come first", () => {
  const early = {
    role: "assistant",
    step_id: "tool-request",
    message_id: "early",
  };
  const final = {
    role: "assistant",
    step_id: "final-model",
    message_id: "final",
  };
  const user = { role: "user", step_id: "final-model", message_id: "user" };
  for (const messages of [
    [early, user, final],
    [final, early, user],
  ])
    expect(selectAssistantMessageForStep(messages, "final-model")).toBe(final);
});

test("final message selection rejects missing or ambiguous producer identity", () => {
  const early = {
    message_id: "early",
    role: "assistant",
    step_id: "tool-request",
  };
  const final = {
    message_id: "final",
    role: "assistant",
    step_id: "final-model",
  };
  expect(() => selectAssistantMessageForStep([early], "final-model")).toThrow(
    "exact final model assistant message required",
  );
  expect(() =>
    selectAssistantMessageForStep([final, { ...final }], "final-model"),
  ).toThrow("exact final model assistant message required");
});
const view = (
  at: string,
  status: string,
  approval: string | null | undefined,
  artifact = false,
): HistoricalView => ({
  at,
  run: {
    status,
    public_summary: status === "completed" ? "actual final" : null,
  },
  approvals:
    approval === undefined
      ? []
      : [{ decision: approval, status: approval ? "decided" : "waiting" }],
  artifacts: artifact ? [{}] : [],
});
test("selects observed semantic boundaries without decoding opaque cursors or wall-clock arithmetic", () => {
  const values = [
    view("opaque:z", "running", undefined),
    view("opaque:a", "waiting", null),
    view("opaque:k", "running", "approved"),
    view("opaque:b", "completed", "approved", true),
  ];
  expect(selectHistoryCuts(values)).toEqual({
    before: values[0],
    pending: values[1],
    decided: values[2],
    produced: values[3],
  });
});
test("fails closed when no approved pre-artifact observation exists", () => {
  expect(() =>
    selectHistoryCuts([
      view("one", "running", undefined),
      view("two", "waiting", null),
      view("three", "completed", "approved", true),
    ]),
  ).toThrow("historical boundary");
});
test("future summary on pending cut is rejected", () => {
  const pending = view("two", "waiting", null);
  pending.run.public_summary = "leaked future";
  expect(() =>
    selectHistoryCuts([
      view("one", "running", undefined),
      pending,
      view("three", "running", "approved"),
      view("four", "completed", "approved", true),
    ]),
  ).toThrow("historical boundary");
});
