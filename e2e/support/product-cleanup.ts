import type { Page } from "@playwright/test";

import { appApi } from "./api";
import type { CleanupAction } from "./cleanup-journal";

type ProductResource = Extract<
  CleanupAction,
  { action: "delete-resource" }
>["resource"];

type SessionState = {
  status: "pending" | "running" | "waiting" | "completed" | "cancelled" | "failed";
};

type PatrolPackState = {
  status: "draft" | "validating" | "active" | "paused" | "invalid";
};

const RESOURCE_PATHS = {
  "knowledge-base": "/knowledge-bases",
  session: "/sessions",
  team: "/teams",
  "patrol-pack": "/patrol-packs",
  "mcp-server": "/integrations/mcp-servers",
  "a2a-server": "/integrations/a2a-servers",
  "inference-model": "/inference/models",
  memory: "/memories",
} as const satisfies Record<ProductResource, string>;

export async function cleanupProductResource(
  page: Page,
  resource: ProductResource,
  resourceId: string,
  options: { workspaceId?: string } = {},
): Promise<void> {
  const collection = RESOURCE_PATHS[resource];
  const resourcePath = `${collection}/${encodeURIComponent(resourceId)}`;
  const scopedHeaders = options.workspaceId
    ? { "X-Workspace-Id": options.workspaceId }
    : undefined;
  if (resource === "session") {
    const current = await appApi<SessionState | null>(page, resourcePath, {
      headers: scopedHeaders,
      expectStatus: [200, 404],
    });
    if (current.status === 404) return;
    if (current.data?.status === "running" || current.data?.status === "waiting") {
      await appApi(page, `${resourcePath}/stop`, {
        method: "POST",
        body: {},
        headers: scopedHeaders,
        expectStatus: [200, 409],
      });
      const deadline = Date.now() + 30_000;
      while (Date.now() < deadline) {
        const observed = await appApi<SessionState | null>(page, resourcePath, {
          headers: scopedHeaders,
          expectStatus: [200, 404],
        });
        if (
          observed.status === 404 ||
          observed.data?.status === "completed" ||
          observed.data?.status === "cancelled" ||
          observed.data?.status === "failed"
        ) {
          break;
        }
        await page.waitForTimeout(250);
      }
    }
    await appApi(page, `${resourcePath}/delete`, {
      method: "POST",
      headers: scopedHeaders,
      expectStatus: [200, 404],
    });
    return;
  }
  if (resource === "patrol-pack") {
    const current = await appApi<PatrolPackState | null>(page, resourcePath, {
      headers: scopedHeaders,
      expectStatus: [200, 404],
    });
    if (current.status === 404) return;
    if (current.data?.status === "active") {
      await appApi(page, `${resourcePath}/pause`, {
        method: "POST",
        body: {},
        headers: scopedHeaders,
        expectStatus: [200, 404],
      });
    }
  }
  await appApi(page, resourcePath, {
    method: "DELETE",
    headers: scopedHeaders,
    expectStatus: [200, 404],
  });
}
