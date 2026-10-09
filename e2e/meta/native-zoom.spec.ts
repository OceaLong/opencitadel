import { test, expect } from "@playwright/test";
import { browserZoom } from "../fixtures/native-zoom.fixture";

test("native zoom API adapter binds one exact owned tab and verifies automatic factor", async () => {
  const previous = process.env.PLAYWRIGHT_BASE_URL;
  process.env.PLAYWRIGHT_BASE_URL = "http://owned.invalid";
  let tabs = [{ id: 7, url: "http://owned.invalid/analysis" }];
  let factor = 1;
  let mode = "automatic";
  const calls: unknown[] = [];
  const api = {
    query: async () => tabs,
    setZoomSettings: async (id: number, settings: unknown) => {
      calls.push(["settings", id, settings]);
    },
    setZoom: async (id: number, value: number) => {
      calls.push(["zoom", id, value]);
      factor = value;
    },
    getZoom: async (id: number) => {
      calls.push(["read", id]);
      return factor;
    },
    getZoomSettings: async () => ({ mode, scope: "per-tab" }),
  };
  // Synthetic API behavior only: no browser launch or native zoom claim.
  const worker = {
    evaluate: async (fn: any, arg: any) => {
      const original = (globalThis as any).chrome;
      (globalThis as any).chrome = { tabs: api };
      try {
        return await fn(arg);
      } finally {
        (globalThis as any).chrome = original;
      }
    },
  } as any;
  const page = { url: () => "http://owned.invalid/analysis" } as any;
  try {
    expect((await browserZoom(worker, page, 2)).factor).toBe(2);
    expect(calls).toEqual([
      ["settings", 7, { mode: "automatic", scope: "per-tab" }],
      ["zoom", 7, 2],
      ["read", 7],
    ]);
    mode = "manual";
    await expect(browserZoom(worker, page, 2)).rejects.toThrow("not applied");
    tabs = [...tabs, { id: 8, url: page.url() }];
    await expect(browserZoom(worker, page, 2)).rejects.toThrow("ambiguous");
    await expect(
      browserZoom(worker, { url: () => "http://foreign.invalid" } as any, 2),
    ).rejects.toThrow("owned");
  } finally {
    if (previous === undefined) delete process.env.PLAYWRIGHT_BASE_URL;
    else process.env.PLAYWRIGHT_BASE_URL = previous;
  }
});
