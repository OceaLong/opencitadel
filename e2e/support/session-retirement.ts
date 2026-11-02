import type { Page } from "@playwright/test";

import {
  completeCleanupAction,
  registerCleanupAction,
  type CleanupEntry,
} from "./cleanup-journal";
import { cleanupProductResource } from "./product-cleanup";

// A browser page is the exact authenticated creator surface. Never consume a
// global journal delta: other projects may be creating sessions concurrently.
const owned = new WeakMap<Page, Map<string, CleanupEntry>>();

export async function registerOwnedSession(
  page: Page,
  sessionId: string,
  workspaceId?: string | null,
): Promise<CleanupEntry> {
  const workspace =
    workspaceId === undefined
      ? await page.evaluate(() =>
          localStorage.getItem("opencitadel-active-workspace"),
        )
      : workspaceId;
  const key = JSON.stringify([sessionId, workspace ?? null]);
  let sessions = owned.get(page);
  if (!sessions) {
    sessions = new Map();
    owned.set(page, sessions);
  }
  const existing = sessions.get(key);
  if (existing) return existing;
  const entry = registerCleanupAction({
    action: "delete-resource",
    resource: "session",
    resource_id: sessionId,
    ...(workspace ? { workspace_id: workspace } : {}),
  });
  sessions.set(key, entry);
  return entry;
}

/** Soft deletion releases the sandbox after all case-owned evidence reads. */
export async function retireOwnedSessions(page: Page): Promise<void> {
  const sessions = owned.get(page);
  if (!sessions) return;
  const errors: unknown[] = [];
  const previousWorkspace = await page.evaluate(() =>
    localStorage.getItem("opencitadel-active-workspace"),
  );
  for (const [key, entry] of sessions) {
    const value = entry.value;
    if (value.action !== "delete-resource" || value.resource !== "session")
      throw new Error("owned session cleanup identity mismatch");
    try {
      // A case may end in scope B after creating a personal/scope A session.
      // Restore the exact creation scope before appApi applies browser headers.
      await page.evaluate((workspace) => {
        if (workspace)
          localStorage.setItem("opencitadel-active-workspace", workspace);
        else localStorage.removeItem("opencitadel-active-workspace");
      }, value.workspace_id ?? null);
      await cleanupProductResource(page, "session", value.resource_id, {
        workspaceId: value.workspace_id,
      });
      completeCleanupAction(entry);
      sessions.delete(key);
    } catch (error) {
      // Keep the durable pending entry for final cleanup, including test errors.
      errors.push(error);
    }
  }
  try {
    await page.evaluate((workspace) => {
      if (workspace)
        localStorage.setItem("opencitadel-active-workspace", workspace);
      else localStorage.removeItem("opencitadel-active-workspace");
    }, previousWorkspace);
  } catch (error) {
    errors.push(error);
  }
  if (errors.length)
    throw new AggregateError(errors, "owned case session retirement failed");
}

export async function useWithSessionRetirement(
  page: Page,
  use: (page: Page) => Promise<void>,
): Promise<void> {
  const errors: unknown[] = [];
  try {
    await use(page);
  } catch (error) {
    errors.push(error);
  }
  try {
    await retireOwnedSessions(page);
  } catch (error) {
    errors.push(error);
  }
  if (errors.length === 1) throw errors[0];
  if (errors.length > 1)
    throw new AggregateError(
      errors,
      "case and owned session retirement failed",
    );
}
