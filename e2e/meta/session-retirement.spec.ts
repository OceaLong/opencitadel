import { expect, test, type Page, type TestInfo } from "@playwright/test";

import {
  completeCleanupAction,
  readCleanupActions,
} from "../support/cleanup-journal";
import { cleanupProductResource } from "../support/product-cleanup";
import {
  registerOwnedSession,
  retireOwnedSessions,
  useWithSessionRetirement,
} from "../support/session-retirement";

function journal(info: TestInfo) {
  const previous = {
    ACCEPTANCE_RUN_ID: process.env.ACCEPTANCE_RUN_ID,
    ACCEPTANCE_EVIDENCE_DIR: process.env.ACCEPTANCE_EVIDENCE_DIR,
  };
  process.env.ACCEPTANCE_RUN_ID = "case-retirement-proof";
  process.env.ACCEPTANCE_EVIDENCE_DIR = info.outputPath("journal");
  return () => {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  };
}

function creatorPage(workspace: string | null, status = "completed") {
  const requests: Array<{ path: string; method: string; workspace?: string }> =
    [];
  const removed = new Set<string>();
  const rejected = new Set<string>();
  const page = {
    evaluate: async (_callback: unknown, args?: any) => {
      if (args === undefined) return workspace;
      if (args === null || typeof args === "string") {
        workspace = args;
        return;
      }
      const { requestPath: path, requestInit: init } = args;
      const session = path.split("/")[2];
      requests.push({
        path,
        method: init.method,
        workspace: init.headers?.["X-Workspace-Id"] ?? workspace ?? undefined,
      });
      if (rejected.has(session)) throw new Error("owned deletion unavailable");
      let response = 200;
      let data: unknown = { status };
      if (removed.has(session)) response = 404;
      else if (path.endsWith("/stop")) status = "completed";
      else if (path.endsWith("/delete")) removed.add(session);
      return {
        status: response,
        payload: { code: response, msg: "observed", data },
      };
    },
    waitForTimeout: async () => {},
  } as unknown as Page;
  return { page, requests, removed, rejected, workspace: () => workspace };
}

test("personal session cleanup cannot be mistaken for a 404 in the workspace selected later", async ({}, info) => {
  const restore = journal(info);
  try {
    const owner = creatorPage(null);
    await registerOwnedSession(owner.page, "personal-session");
    await owner.page.evaluate(() => {}, "later-team");
    await retireOwnedSessions(owner.page);
    expect(
      owner.requests.every((request) => request.workspace === undefined),
    ).toBe(true);
    expect(owner.removed.has("personal-session")).toBe(true);
    expect(owner.workspace()).toBe("later-team");
    expect(readCleanupActions()).toEqual([]);
  } finally {
    restore();
  }
});

test("case retirement releases only its exact creator page and captured workspace, then journal replay is idempotent", async ({}, info) => {
  const restore = journal(info);
  try {
    const owner = creatorPage("owned-team");
    const foreign = creatorPage("other-team");
    const entry = await registerOwnedSession(owner.page, "owned-session");
    await registerOwnedSession(foreign.page, "parallel-session");
    await retireOwnedSessions(owner.page);
    expect(owner.requests).toEqual([
      {
        path: "/sessions/owned-session",
        method: "GET",
        workspace: "owned-team",
      },
      {
        path: "/sessions/owned-session/delete",
        method: "POST",
        workspace: "owned-team",
      },
    ]);
    expect(foreign.requests).toEqual([]);
    expect(readCleanupActions().map((item) => item.value)).toEqual([
      expect.objectContaining({ resource_id: "parallel-session" }),
    ]);
    await retireOwnedSessions(owner.page);
    expect(owner.requests).toHaveLength(2);
    await cleanupProductResource(owner.page, "session", "owned-session", {
      workspaceId: "owned-team",
    });
    completeCleanupAction(entry);
    expect(owner.requests.at(-1)?.method).toBe("GET");
    await retireOwnedSessions(foreign.page);
    expect(readCleanupActions()).toEqual([]);
  } finally {
    restore();
  }
});

test("a failed case still stops and retires its waiting session without replacing the original failure", async ({}, info) => {
  const restore = journal(info);
  try {
    const owner = creatorPage(null, "waiting");
    const failure = new Error("original case assertion failed");
    await expect(
      useWithSessionRetirement(owner.page, async () => {
        await registerOwnedSession(owner.page, "waiting-session", null);
        throw failure;
      }),
    ).rejects.toBe(failure);
    expect(owner.requests.map((item) => [item.method, item.path])).toEqual([
      ["GET", "/sessions/waiting-session"],
      ["POST", "/sessions/waiting-session/stop"],
      ["GET", "/sessions/waiting-session"],
      ["POST", "/sessions/waiting-session/delete"],
    ]);
    expect(readCleanupActions()).toEqual([]);
  } finally {
    restore();
  }
});

test("retirement failure keeps the exact durable obligation and still releases other owned sessions", async ({}, info) => {
  const restore = journal(info);
  try {
    const owner = creatorPage(null);
    owner.rejected.add("blocked-session");
    await registerOwnedSession(owner.page, "blocked-session", null);
    await registerOwnedSession(owner.page, "ready-session", null);
    const caseFailure = new Error("original case failure");
    let failure: AggregateError | undefined;
    try {
      await useWithSessionRetirement(owner.page, async () => {
        throw caseFailure;
      });
    } catch (error) {
      failure = error as AggregateError;
    }
    expect(failure?.errors[0]).toBe(caseFailure);
    expect(failure?.errors[1]).toBeInstanceOf(AggregateError);
    expect(owner.removed.has("ready-session")).toBe(true);
    expect(readCleanupActions().map((item) => item.value)).toEqual([
      expect.objectContaining({ resource_id: "blocked-session" }),
    ]);
    owner.rejected.clear();
    await retireOwnedSessions(owner.page);
    expect(readCleanupActions()).toEqual([]);
  } finally {
    restore();
  }
});
