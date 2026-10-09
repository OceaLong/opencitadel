import { chromium, type Page, type Worker } from "@playwright/test";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { createHash } from "node:crypto";
import { test as acceptance, expect } from "./acceptance.fixture";

export async function browserZoom(worker: Worker, page: Page, factor: 1 | 2) {
  const url = page.url();
  const expected = new URL(process.env.PLAYWRIGHT_BASE_URL!);
  if (new URL(url).origin !== expected.origin)
    throw new Error("zoom target is not owned acceptance origin");
  return worker.evaluate(
    async ({ url, factor }) => {
      const api = (globalThis as any).chrome.tabs;
      const tabs = (await api.query({})).filter((tab: any) => tab.url === url);
      if (tabs.length !== 1 || typeof tabs[0].id !== "number")
        throw new Error("ambiguous owned zoom tab");
      await api.setZoomSettings(tabs[0].id, {
        mode: "automatic",
        scope: "per-tab",
      });
      await api.setZoom(tabs[0].id, factor);
      const actual = await api.getZoom(tabs[0].id);
      const settings = await api.getZoomSettings(tabs[0].id);
      if (
        actual !== factor ||
        settings.mode !== "automatic" ||
        settings.scope !== "per-tab"
      )
        throw new Error("native zoom was not applied");
      return { tabId: tabs[0].id, url, factor: actual, settings };
    },
    { url, factor },
  );
}

export const zoomTest = acceptance.extend<{
  nativeZoom: (page: Page) => Promise<void>;
}>({
  context: async ({ baseURL, locale, colorScheme }, use) => {
    const profile = await mkdtemp(resolve(tmpdir(), "opencitadel-owned-zoom-"));
    const extension = resolve(__dirname, "native-zoom-extension");
    let context;
    try {
      context = await chromium.launchPersistentContext(profile, {
        channel: "chromium",
        headless: false,
        baseURL,
        locale,
        colorScheme,
        reducedMotion: "reduce",
        viewport: null,
        args: [
          `--disable-extensions-except=${extension}`,
          `--load-extension=${extension}`,
          "--window-size=1440,1000",
        ],
      });
      await use(context);
    } finally {
      const failures: unknown[] = [];
      if (context) {
        try {
          await context.close();
        } catch (error) {
          failures.push(error);
        }
      }
      try {
        await rm(profile, { recursive: true, force: true });
      } catch (error) {
        failures.push(error);
      }
      if (failures.length)
        throw new AggregateError(failures, "owned native zoom cleanup failed");
    }
  },
  nativeZoom: async ({ context }, use, info) => {
    const worker =
      context.serviceWorkers()[0] ??
      (await context.waitForEvent("serviceworker", { timeout: 30_000 }));
    if (!worker.url().startsWith("chrome-extension://"))
      throw new Error("native zoom worker absent");
    const extension = resolve(__dirname, "native-zoom-extension");
    const digest = createHash("sha256")
      .update(await readFile(resolve(extension, "manifest.json")))
      .update(await readFile(resolve(extension, "background.js")))
      .digest("hex");
    const pages = new Set<Page>();
    let sequence = 0;
    const geometry = (page: Page) =>
      page.evaluate(() => ({
        innerWidth,
        innerHeight,
        outerWidth,
        outerHeight,
        dpr: devicePixelRatio,
        client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
      }));
    try {
      await use(async (page) => {
        pages.add(page);
        sequence++;
        await browserZoom(worker, page, 1);
        const before = await geometry(page);
        const native = await browserZoom(worker, page, 2);
        await expect
          .poll(async () => (await geometry(page)).innerWidth)
          .toBeLessThan(before.innerWidth * 0.6);
        const after = await geometry(page);
        expect(after.outerWidth).toBe(before.outerWidth);
        expect(after.outerHeight).toBe(before.outerHeight);
        expect(after.dpr / before.dpr).toBeGreaterThan(1.8);
        expect(after.scroll).toBeLessThanOrEqual(after.client + 1);
        await info.attach(`native-zoom-${sequence}.json`, {
          body: JSON.stringify({
            run_id: process.env.ACCEPTANCE_RUN_ID,
            project: process.env.ACCEPTANCE_PROJECT_ID,
            browser: context.browser()?.version(),
            extension_sha256: digest,
            native,
            before,
            after,
          }),
          contentType: "application/json",
        });
        await info.attach(`native-zoom-${sequence}.png`, {
          body: await page.screenshot(),
          contentType: "image/png",
        });
      });
    } finally {
      for (const page of pages)
        if (
          !page.isClosed() &&
          new URL(page.url()).origin ===
            new URL(process.env.PLAYWRIGHT_BASE_URL!).origin
        )
          await browserZoom(worker, page, 1);
    }
  },
});
