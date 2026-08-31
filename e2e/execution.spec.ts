import { randomUUID } from "node:crypto";

import type { Page } from "@playwright/test";

import { appApi, expect, test } from "./fixtures/acceptance.fixture";
import { registerCleanupAction } from "./support/cleanup-journal";
import { acceptanceId } from "./support/ids";
import { pollProjection } from "./support/poll";

type SessionResponse = {
  session_id: string;
  status: "pending" | "running" | "waiting" | "completed" | "cancelled" | "failed";
  events?: ExecutionEvent[];
};

type ExecutionEvent = {
  cursor: string;
  event_id: string;
  event_type: string;
  run_id: string | null;
  stream_id: string;
  stream_version: number;
  payload: Record<string, unknown>;
};

type ExecutionEventPage = {
  events: ExecutionEvent[];
  next_cursor: string | null;
  prev_cursor: string | null;
  has_earlier: boolean;
};

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

type MemoryList = {
  entries: Array<{ id: string; title: string; content: string; source: string }>;
};

type InferenceModel = { id: string; model_name: string; kind: string };

type ChatBody = {
  message?: string;
  request_id?: string;
  event_id?: string;
  model_id?: string;
  mode?: "ask" | "agent";
};

function cover(...requirementIds: string[]): void {
  for (const requirementId of requirementIds) {
    test
      .info()
      .annotations.push({ type: "acceptance", description: requirementId });
  }
}

async function createSession(
  page: Page,
  title: string,
  mode: "ask" | "agent",
  modelId?: string,
): Promise<string> {
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
  });
  return session.data.session_id;
}

async function governanceProfile(
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

async function readChatStream(
  page: Page,
  sessionId: string,
  body: ChatBody,
  options: { stopAfter?: number; timeoutMs?: number } = {},
): Promise<StreamEvent[]> {
  return page.evaluate(
    async ({ sessionId, body, stopAfter, timeoutMs }) => {
      const csrf = document.cookie
        .split("; ")
        .find((cookie) => cookie.startsWith("csrf_token="))
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
          buffer += decoder.decode(value, { stream: !done }).replaceAll(
            "\r\n",
            "\n",
          );
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

function assertUniqueCursors(events: readonly StreamEvent[]): void {
  expect(new Set(events.map((event) => event.cursor)).size).toBe(events.length);
  for (const event of events) {
    expect(event.data.event_id).toBe(event.cursor);
  }
}

async function waitForTerminalProfile(
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

test.describe.configure({ mode: "serial" });

test("Agent and Ask terminate through formal retrieval and model Activities", async ({
  operatorPage: page,
}) => {
  test.setTimeout(240_000);
  cover("RUN-AGENT", "RUN-ASK");

  for (const mode of ["agent", "ask"] as const) {
    const prompt = `ordinary ${mode} ${acceptanceId("conversation")}`;
    const sessionId = await createSession(
      page,
      acceptanceId(`${mode}-session`),
      mode,
    );
    const streamed = await readChatStream(page, sessionId, {
      message: prompt,
      request_id: randomUUID(),
      mode,
    });

    assertUniqueCursors(streamed);
    expect(streamed.at(-1)?.type).toBe("done");
    const assistant = streamed.find(
      (event) =>
        event.type === "message" && event.data.role === "assistant",
    );
    expect(assistant?.data.message).toBe(`Acceptance response: ${prompt}`);

    const profile = await waitForTerminalProfile(
      page,
      sessionId,
      "completed",
    );
    expect(profile.chain.verified).toBe(true);
    expect(profile.runs[0]).toMatchObject({ family: mode, status: "completed" });
    expect(
      profile.activities.map((activity) => [
        activity.activity_type,
        activity.status,
      ]),
    ).toEqual([
      ["retrieval.search", "succeeded"],
      ["model.call", "succeeded"],
    ]);
  }
});

test("SSE reconnect from a formal cursor has no duplicates or missing events", async ({
  operatorPage: page,
}) => {
  test.setTimeout(180_000);
  cover("RUN-SSE");

  const sessionId = await createSession(
    page,
    acceptanceId("sse-session"),
    "ask",
  );
  const first = await readChatStream(
    page,
    sessionId,
    {
      message: `cursor replay ${acceptanceId("sse")}`,
      request_id: randomUUID(),
      mode: "ask",
    },
    { stopAfter: 2 },
  );
  expect(first).toHaveLength(2);
  assertUniqueCursors(first);
  const replayCursor = first.at(-1)?.cursor;
  if (!replayCursor) throw new Error("disconnect cursor is missing");

  const resumed = await readChatStream(page, sessionId, {
    event_id: replayCursor,
  });
  expect(resumed.at(-1)?.type).toBe("done");
  assertUniqueCursors(resumed);
  expect(first.map((event) => event.cursor)).not.toContain(
    resumed[0]?.cursor,
  );

  await waitForTerminalProfile(page, sessionId, "completed");
  const persisted = (
    await appApi<ExecutionEventPage>(
      page,
      `/sessions/${encodeURIComponent(sessionId)}/events?limit=100`,
    )
  ).data.events;
  expect([...first, ...resumed].map((event) => event.cursor)).toEqual(
    persisted.map((event) => event.cursor),
  );
});

test("approval executes one declared external effect and rejection executes none", async ({
  operatorPage: page,
}) => {
  test.setTimeout(300_000);
  cover("RUN-APPROVE", "RUN-REJECT");

  const before = (await appApi<MemoryList>(page, "/memories?q=acceptance"))
    .data.entries;
  const beforeIds = new Set(before.map((entry) => entry.id));

  const approvedSessionId = await createSession(
    page,
    acceptanceId("approval-session"),
    "agent",
  );
  const approvalStream = await readChatStream(page, approvedSessionId, {
    message: `[acceptance:tool:memory_save] ${acceptanceId("approve")}`,
    request_id: randomUUID(),
    mode: "agent",
  });
  const approvalEvent = approvalStream.at(-1);
  expect(approvalEvent?.type).toBe("approval");
  expect(approvalEvent?.data.options).toEqual(["approve", "reject"]);
  expect(approvalEvent?.data.payload).toMatchObject({
    tool_name: "memory_save",
  });

  await page.goto(`/sessions/${encodeURIComponent(approvedSessionId)}`);
  await expect(
    page.getByRole("button", { name: /^Approve$|^批准$/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^Approve$|^批准$/ }).click();
  const approvedProfile = await waitForTerminalProfile(
    page,
    approvedSessionId,
    "completed",
  );
  await expect(page.getByText(/Acceptance tool result:/)).toBeVisible({
    timeout: 60_000,
  });
  expect(approvedProfile.approvals).toHaveLength(1);
  expect(approvedProfile.approvals[0]).toMatchObject({
    subject_label: "memory_save",
    status: "approved",
    decision: "approved",
  });
  expect(
    approvedProfile.activities.filter(
      (activity) => activity.activity_type === "tool.call",
    ),
  ).toHaveLength(1);

  const afterApproval = await pollProjection(
    async () => (await appApi<MemoryList>(page, "/memories?q=acceptance"))
      .data.entries,
    (entries) => entries.filter((entry) => !beforeIds.has(entry.id)).length === 1,
    { timeout: 60_000, message: "approved memory effect appears once" },
  );
  const createdMemory = afterApproval.filter(
    (entry) => !beforeIds.has(entry.id),
  );
  expect(createdMemory).toHaveLength(1);
  expect(createdMemory[0]).toMatchObject({
    title: "acceptance",
    content: "acceptance",
    source: "tool_save",
  });
  registerCleanupAction({
    action: "delete-resource",
    resource: "memory",
    resource_id: createdMemory[0]!.id,
  });

  const rejectedSessionId = await createSession(
    page,
    acceptanceId("rejection-session"),
    "agent",
  );
  const rejectionStream = await readChatStream(page, rejectedSessionId, {
    message: `[acceptance:tool:memory_save] ${acceptanceId("reject")}`,
    request_id: randomUUID(),
    mode: "agent",
  });
  expect(rejectionStream.at(-1)?.type).toBe("approval");

  await page.goto(`/sessions/${encodeURIComponent(rejectedSessionId)}`);
  await page.getByRole("button", { name: /^Reject$|^拒绝$/ }).click();
  await page
    .getByPlaceholder(/^Reason for rejection\.\.\.$|^拒绝原因\.\.\.$/)
    .fill("acceptance rejection");
  await page
    .getByRole("button", { name: /^Confirm reject$|^确认拒绝$/ })
    .click();
  const rejectedProfile = await waitForTerminalProfile(
    page,
    rejectedSessionId,
    "cancelled",
  );
  expect(rejectedProfile.approvals[0]).toMatchObject({
    subject_label: "memory_save",
    status: "rejected",
    decision: "rejected",
  });
  expect(
    rejectedProfile.activities.filter(
      (activity) => activity.activity_type === "tool.call",
    ),
  ).toHaveLength(0);
  const afterRejection = (
    await appApi<MemoryList>(page, "/memories?q=acceptance")
  ).data.entries;
  const survivingCreatedMemories = afterRejection.filter(
    (entry) => !beforeIds.has(entry.id),
  );
  expect(survivingCreatedMemories.map((entry) => entry.id)).toEqual([
    createdMemory[0]!.id,
  ]);
  expect(survivingCreatedMemories[0]).toMatchObject({
    title: "acceptance",
    content: "acceptance",
    source: "tool_save",
  });
});

test("cancellation converges Run, session, Activity, and reloaded UI", async ({
  operatorPage: page,
  bootstrapState,
}) => {
  test.setTimeout(240_000);
  cover("RUN-CANCEL");

  if (!bootstrapState.endpoint_id) {
    throw new Error("bootstrap inference endpoint is missing");
  }
  const failureModel = (
    await appApi<InferenceModel>(page, "/inference/models", {
      method: "POST",
      body: {
        endpoint_id: bootstrapState.endpoint_id,
        display_name: acceptanceId("failure-model"),
        model_name: "acceptance-failure",
        kind: "chat",
        settings: { kind: "chat", temperature: 0, max_output_tokens: 4096 },
        input_price_per_million: 0,
        output_price_per_million: 0,
        extra_params: {},
        capabilities: {},
        visibility: "global",
      },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "inference-model",
    resource_id: failureModel.id,
  });

  const sessionId = await createSession(
    page,
    acceptanceId("cancellation-session"),
    "agent",
    failureModel.id,
  );
  const disconnected = await readChatStream(
    page,
    sessionId,
    {
      message: `[acceptance:timeout] ${acceptanceId("cancel")}`,
      request_id: randomUUID(),
      model_id: failureModel.id,
      mode: "agent",
    },
    { stopAfter: 2 },
  );
  expect(disconnected).toHaveLength(2);

  await pollProjection(
    () => governanceProfile(page, sessionId),
    (profile) =>
      profile.runs[0]?.status === "running" &&
      profile.activities.some(
        (activity) =>
          activity.activity_type === "model.call" &&
          activity.status === "running",
      ),
    {
      timeout: 90_000,
      intervals: [100, 250, 500, 1_000],
      message: "timeout model Activity begins before cancellation",
    },
  );
  await appApi(page, `/sessions/${encodeURIComponent(sessionId)}/stop`, {
    method: "POST",
    body: {},
  });

  const cancelled = await waitForTerminalProfile(
    page,
    sessionId,
    "cancelled",
  );
  expect(cancelled.activities).toContainEqual(
    expect.objectContaining({
      activity_type: "model.call",
      status: "cancelled",
      failure_code: "ACTIVITY_CANCELLED",
    }),
  );
  expect(
    cancelled.activities.every((activity) =>
      ["succeeded", "failed", "unknown", "cancelled"].includes(
        activity.status,
      ),
    ),
  ).toBe(true);

  const session = (
    await appApi<SessionResponse>(
      page,
      `/sessions/${encodeURIComponent(sessionId)}`,
    )
  ).data;
  expect(session.status).toBe("cancelled");
  await page.goto(`/sessions/${encodeURIComponent(sessionId)}`);
  await expect(page.getByText(/^Task cancelled\.$|^任务已取消。$/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/^Task cancelled\.$|^任务已取消。$/)).toBeVisible();
});
