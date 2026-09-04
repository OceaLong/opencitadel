// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import type { AskEventData, SSEEventData } from "@/lib/api/types";

import { getLatestAskFromEvents } from "./use-session-detail-view";

function askEvent(data: Partial<AskEventData> & { ask_id: string; status: string }): SSEEventData {
  return {
    type: "ask",
    data: {
      schema_version: 1,
      visibility: "user",
      channel: "ui",
      persist: true,
      created_at: 1725400000,
      ...data,
    } as AskEventData,
  };
}

const pendingAsk = askEvent({
  ask_id: "ask-1",
  status: "pending",
  question: "Which environment?",
  choices: ["Staging", "Production"],
  tool_name: "ask_user",
});

const messageEvent: SSEEventData = {
  type: "message",
  data: {
    role: "assistant",
    message: "working on it",
    schema_version: 1,
    visibility: "user",
    channel: "ui",
    persist: true,
    created_at: 1725400001,
  },
};

describe("getLatestAskFromEvents", () => {
  it("returns the latest pending ask while the session is waiting", () => {
    const result = getLatestAskFromEvents([messageEvent, pendingAsk], true);

    expect(result).not.toBeNull();
    expect(result?.ask_id).toBe("ask-1");
    expect(result?.choices).toEqual(["Staging", "Production"]);
  });

  it("returns null when the session is not waiting", () => {
    expect(getLatestAskFromEvents([pendingAsk], false)).toBeNull();
  });

  it("returns null when a later resolved event exists for the same ask", () => {
    const resolved = askEvent({ ask_id: "ask-1", status: "resolved", choice: "Staging" });

    expect(getLatestAskFromEvents([pendingAsk, resolved], true)).toBeNull();
  });

  it("returns null when a later declined or expired event exists for the same ask", () => {
    const declined = askEvent({ ask_id: "ask-1", status: "declined", choice: "" });
    const expired = askEvent({ ask_id: "ask-1", status: "expired" });

    expect(getLatestAskFromEvents([pendingAsk, declined], true)).toBeNull();
    expect(getLatestAskFromEvents([pendingAsk, expired], true)).toBeNull();
  });

  it("returns null when there is no ask event at all", () => {
    expect(getLatestAskFromEvents([messageEvent], true)).toBeNull();
  });
});
