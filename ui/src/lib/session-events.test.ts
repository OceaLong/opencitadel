import { describe, expect, it } from "vitest";

import type { SSEEventData } from "@/lib/api/types";

import { eventsToTimeline } from "./session-events";

describe("eventsToTimeline error i18n", () => {
  it("prefers localized quota message when error code is present", () => {
    const events: SSEEventData[] = [
      {
        type: "error",
        data: {
          error:
            "Error code: 403 - {'error': {'code': 'insufficient_quota', 'message': 'The free quota has been exhausted.'}}",
          code: "MODEL_QUOTA_EXCEEDED",
          created_at: 1,
        },
      },
    ];

    const timeline = eventsToTimeline(events);
    const errorItem = timeline.find((item) => item.kind === "error");

    expect(errorItem?.kind).toBe("error");
    if (errorItem?.kind === "error") {
      expect(errorItem.error).not.toContain("insufficient_quota");
      expect(errorItem.error.length).toBeGreaterThan(0);
    }
  });

  it("falls back to raw error text when code is missing", () => {
    const events: SSEEventData[] = [
      {
        type: "error",
        data: {
          error: "plain backend error",
          created_at: 1,
        },
      },
    ];

    const timeline = eventsToTimeline(events);
    const errorItem = timeline.find((item) => item.kind === "error");

    expect(errorItem?.kind).toBe("error");
    if (errorItem?.kind === "error") {
      expect(errorItem.error).toBe("plain backend error");
    }
  });
});
