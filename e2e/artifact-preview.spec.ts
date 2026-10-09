import { createRequire } from "node:module";
import { resolve } from "node:path";
import { test, expect } from "@playwright/test";

const uiRoot = resolve(__dirname, "../ui");
const uiRequire = createRequire(resolve(uiRoot, "package.json"));

// Bounded local harness imports the actual production component through Vite.
// Model direct-Next absent parent CSP and the current nginx parent policy.
// This is component isolation proof, not full deployment/UI acceptance.
for (const parentPolicy of ["direct", "nginx"]) {
  test(`artifact preview denies requests, navigation and app DOM access (${parentPolicy})`, async ({
    page,
  }) => {
    const { createServer } = uiRequire("vite");
    const server = await createServer({
      configFile: false,
      root: uiRoot,
      appType: "custom",
      // No stylesheet is imported by this security harness; do not load the app Tailwind build plugin.
      css: { postcss: { plugins: [] } },
      cacheDir: resolve(
        __dirname,
        "../test-results/preview-cache-" + parentPolicy,
      ),
      server: { host: "127.0.0.1", port: 0 },
      plugins: [
        {
          name: "preview-proof",
          resolveId(id: string) {
            if (id === "/proof.tsx") return id;
          },
          load(id: string) {
            if (id === "/proof.tsx")
              return `import React from 'react';
import {createRoot} from 'react-dom/client';
import {SafeArtifactPreview} from '/src/components/session/safe-artifact-preview.tsx';
createRoot(document.getElementById('root')).render(React.createElement(SafeArtifactPreview,{title:'Production preview',notice:'Static preview: active content omitted',content:window.attack}));`;
          },
        },
      ],
    });
    const hits: string[] = [];
    server.middlewares.use((req, res, next) => {
      if (req.url?.startsWith("/leak")) {
        hits.push(req.url);
        res.end("blocked fixture reached");
        return;
      }
      if (req.url !== "/") return next();
      if (parentPolicy === "nginx")
        res.setHeader(
          "Content-Security-Policy",
          "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
        );
      res.setHeader("Content-Type", "text/html");
      res.end(
        '<!doctype html><div id="app-marker">Intact</div><div id="root"></div><script>window.attack=' +
          JSON.stringify(
            `<h1>Readable report</h1><p>Semantic body</p><img src="/leak-img" srcset="/leak-srcset 1x"><div style="background:url(/leak-css)">Styled</div><style>@import '/leak-import';body{background:url(/leak-style)}</style><script>fetch('/leak-fetch');new XMLHttpRequest().open('GET','/leak-xhr');navigator.sendBeacon('/leak-beacon','x');parent.document.getElementById('app-marker').textContent='CORRUPTED';location='/leak-script-nav';<\/script><iframe src="/leak-frame"></iframe><iframe srcdoc="<img src='/leak-srcdoc'>"></iframe><form action="/leak-form"><input name="secret" value="fixture"><button>Submit</button></form><meta http-equiv="refresh" content="0;url=/leak-meta"><a href="/leak-link" target="_top">Click navigation</a><a href="/leak-self">Click self</a><svg><a href="/leak-svg">SVG</a></svg>`,
          ).replaceAll("<", "\\u003c") +
          ';</script><script type="module" src="/proof.tsx"></script>',
      );
    });
    await server.listen();
    try {
      const address = server.httpServer!.address() as { port: number };
      const origin = `http://127.0.0.1:${address.port}`;
      const unexpected: string[] = [];
      await page.route("**/*", (route) => {
        const url = route.request().url();
        if (!url.startsWith(origin + "/")) {
          unexpected.push(url);
          return route.abort();
        }
        return route.continue();
      });
      await page.goto(origin);
      const frame = page.frameLocator('iframe[title="Production preview"]');
      await expect(
        frame.getByRole("heading", { name: "Readable report" }),
      ).toBeVisible();
      await frame.getByText("Click navigation").click();
      await frame.getByText("Click self").click();
      await page.waitForTimeout(350);
      expect(page.url()).toBe(origin + "/");
      expect(page.frames()).toHaveLength(2);
      expect(page.frames()[1].url()).toBe("about:srcdoc");
      await expect(page.locator("#app-marker")).toHaveText("Intact");
      expect(hits).toEqual([]);
      expect(unexpected).toEqual([]);
      await expect(
        frame.locator(
          "script,img,iframe,form,meta[http-equiv=refresh],a[href],style,[style]",
        ),
      ).toHaveCount(0);
    } finally {
      await server.close();
    }
  });
}
