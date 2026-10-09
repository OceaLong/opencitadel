import type { Page } from "@playwright/test";
import { appApi, expect, test } from "../fixtures/acceptance.fixture";
import { registerCleanupAction } from "./cleanup-journal";
import { pollProjection } from "./poll";
type StreamEvent = {
  cursor: string;
  type: string;
  data: Record<string, unknown>;
};

type GovernanceProfile = {
  session: { id: string; status: string };
  chain: { verified: boolean; checked_runs: number; checked_entries: number };
  runs: Array<{
    run_id: string;
    family: string;
    status: string;
    terminal_at: string | null;
  }>;
  approvals: Array<{
    approval_id: string;
    run_id: string;
    subject_activity_id: string;
    subject_label: string;
    status: string;
    decision: string | null;
  }>;
  activities: Array<{
    activity_id: string;
    run_id: string;
    activity_type: string;
    status: string;
    attempt: number;
    failure_code: string | null;
    terminal_at: string | null;
  }>;
};

type ChatBody = {
  message?: string;
  request_id?: string;
  event_id?: string;
  model_id?: string;
  mode?: "ask" | "agent";
};

export function cover(...requirementIds: string[]): void {
  for (const requirementId of requirementIds) {
    test
      .info()
      .annotations.push({ type: "acceptance", description: requirementId });
  }
}

export async function createSession(
  page: Page,
  title: string,
  mode: "ask" | "agent",
  modelId?: string,
): Promise<string> {
  const workspaceId = await page.evaluate(() =>
    localStorage.getItem("opencitadel-active-workspace"),
  );
  const session = await appApi<{ session_id: string }>(page, "/sessions", {
    method: "POST",
    body: {
      title,
      mode,
      ...(modelId ? { model_id: modelId } : {}),
    },
  });
  registerCleanupAction({
    action: "delete-resource",
    resource: "session",
    resource_id: session.data.session_id,
    ...(workspaceId ? { workspace_id: workspaceId } : {}),
  });
  return session.data.session_id;
}

export async function governanceProfile(
  page: Page,
  sessionId: string,
): Promise<GovernanceProfile> {
  return (
    await appApi<GovernanceProfile>(
      page,
      `/admin/governance/sessions/${encodeURIComponent(sessionId)}/profile`,
    )
  ).data;
}

export async function readChatStream(
  page: Page,
  sessionId: string,
  body: ChatBody,
  options: { stopAfter?: number; timeoutMs?: number } = {},
): Promise<StreamEvent[]> {
  return page.evaluate(
    async ({ sessionId, body, stopAfter, timeoutMs }) => {
      const cookies = document.cookie.split("; ");
      const csrf = (
        cookies.find((cookie) => cookie.startsWith("__Host-csrf_token=")) ??
        cookies.find((cookie) => cookie.startsWith("csrf_token="))
      )
        ?.split("=")
        .slice(1)
        .join("=");
      const workspaceId = window.localStorage.getItem(
        "opencitadel-active-workspace",
      );
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), timeoutMs);
      const events: StreamEvent[] = [];
      let buffer = "";

      function parseFrame(frame: string): StreamEvent | null {
        let cursor = "";
        let eventType = "message";
        const data: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("id:")) cursor = line.slice(3).trim();
          if (line.startsWith("event:")) eventType = line.slice(6).trim();
          if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
        }
        if (!cursor || data.length === 0) return null;
        return {
          cursor,
          type: eventType,
          data: JSON.parse(data.join("\n")) as Record<string, unknown>,
        };
      }

      try {
        const response = await fetch(
          `/api/sessions/${encodeURIComponent(sessionId)}/chat`,
          {
            method: "POST",
            credentials: "include",
            headers: {
              Accept: "text/event-stream",
              "Content-Type": "application/json",
              ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
              ...(workspaceId ? { "X-Workspace-Id": workspaceId } : {}),
            },
            body: JSON.stringify(body),
            signal: controller.signal,
          },
        );
        if (!response.ok) {
          throw new Error(
            `chat stream returned HTTP ${response.status}: ${await response.text()}`,
          );
        }
        if (!response.body) throw new Error("chat stream response has no body");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        while (true) {
          const { done, value } = await reader.read();
          buffer += decoder
            .decode(value, { stream: !done })
            .replaceAll("\r\n", "\n");
          let boundary = buffer.indexOf("\n\n");
          while (boundary >= 0) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const event = parseFrame(frame);
            if (event) {
              events.push(event);
              if (stopAfter && events.length >= stopAfter) {
                await reader.cancel();
                controller.abort();
                return events;
              }
            }
            boundary = buffer.indexOf("\n\n");
          }
          if (done) return events;
        }
      } finally {
        window.clearTimeout(timer);
      }
    },
    {
      sessionId,
      body,
      stopAfter: options.stopAfter,
      timeoutMs: options.timeoutMs ?? 120_000,
    },
  );
}

export function assertUniqueCursors(events: readonly StreamEvent[]): void {
  expect(new Set(events.map((event) => event.cursor)).size).toBe(events.length);
  for (const event of events) {
    expect(event.data.event_id).toBe(event.cursor);
  }
}

export async function waitForTerminalProfile(
  page: Page,
  sessionId: string,
  status: "completed" | "cancelled" | "failed",
): Promise<GovernanceProfile> {
  return pollProjection(
    () => governanceProfile(page, sessionId),
    (profile) =>
      profile.session.status === status &&
      profile.runs.length === 1 &&
      profile.runs[0]?.status === status &&
      Boolean(profile.runs[0]?.terminal_at),
    {
      timeout: 120_000,
      intervals: [100, 250, 500, 1_000],
      message: `session ${sessionId} reaches ${status}`,
    },
  );
}
