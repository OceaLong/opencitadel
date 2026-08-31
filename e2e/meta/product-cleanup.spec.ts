import { expect, test, type Page } from "@playwright/test";

import { cleanupProductResource } from "../support/product-cleanup";

test("pauses an active Patrol Pack before deleting it", async () => {
  const requests: Array<{
    requestPath: string;
    requestInit: {
      method: string;
      headers?: Record<string, string>;
    };
  }> = [];
  const page = {
    evaluate: async (
      _callback: unknown,
      value: {
        requestPath: string;
        requestInit: {
          method: string;
          headers?: Record<string, string>;
        };
      },
    ) => {
      requests.push(value);
      const data =
        value.requestInit.method === "GET" ? { status: "active" } : {};
      return {
        status: 200,
        payload: { code: 200, msg: "success", data },
      };
    },
  } as unknown as Page;

  await cleanupProductResource(page, "patrol-pack", "pack-1", {
    workspaceId: "team-1",
  });

  expect(
    requests.map(({ requestPath, requestInit }) => [
      requestInit.method,
      requestPath,
    ]),
  ).toEqual([
    ["GET", "/patrol-packs/pack-1"],
    ["POST", "/patrol-packs/pack-1/pause"],
    ["DELETE", "/patrol-packs/pack-1"],
  ]);
  expect(
    requests.map(({ requestInit }) => requestInit.headers),
  ).toEqual([
    { "X-Workspace-Id": "team-1" },
    { "X-Workspace-Id": "team-1" },
    { "X-Workspace-Id": "team-1" },
  ]);
});
