import { readFileSync } from "node:fs";
import path from "node:path";
import ts from "../ui/node_modules/typescript/lib/typescript.js";
import type * as ContextCodec from "../ui/src/lib/analysis-view/navigation-context";
import { expect, test } from "@playwright/test";
const metric = (value: number | null, unit = "ms", count = 1) => ({
  value,
  unit,
  numerator: null,
  denominator: null,
  sample_count: count,
  missing_count: 1,
  excluded_count: 0,
});
const time = "2026-09-01T00:00:00Z";
const summary = {
  grain: "day",
  timezone: "UTC",
  watermark: "fixed-capture",
  metric_version: "execution-metrics-v1",
  metrics: {
    captured_at: "2026-09-09T00:00:00Z",
    evaluation_series: [],
    series: Array.from({ length: 8 }, (_, i) => ({
      group: {
        bucket: `2026-09-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
        configuration_revision: "configuration-with-a-long-label",
        family: "agent",
        execution_mode: "live",
        purpose: "interactive",
      },
      metrics: {
        success_rate: {
          ...metric(i === 3 ? null : i / 10, "ratio", 10),
          numerator: i,
          denominator: 10,
        },
        latency_p50: metric(i * 100),
        latency_p95: metric(i * 150),
      },
    })),
    charts: {
      latency: {
        scheme: "execution-latency-ms-v1",
        edges_ms: [0, 100, 1000],
        edge_convention: "lower_inclusive_upper_exclusive",
        bin_counts: [1, 0],
        overflow: { lower_ms: 1000, count: 0, maximum_ms: null },
        p50: metric(0),
        p95: metric(0),
        samples: [{ run_id: "run-zero", duration_ms: 0 }],
      },
      latency_groups: [],
      tools: {
        availability: "available",
        items: [
          {
            tool_name: "tool-with-observed-failures",
            terminal: 10,
            errors: 3,
            execution_errors: 2,
            business_errors: 1,
            excluded: 0,
            unknown: 0,
            deferred: 0,
            cancelled: 0,
            error_rate: {
              ...metric(0.3, "ratio", 10),
              numerator: 3,
              denominator: 10,
            },
          },
          {
            tool_name:
              "long-tool-label-for-source-inspection-and-keyboard-verification",
            terminal: 0,
            errors: 0,
            execution_errors: 0,
            business_errors: 0,
            excluded: 1,
            unknown: 1,
            deferred: 0,
            cancelled: 0,
            error_rate: {
              ...metric(null, "ratio"),
              numerator: 0,
              denominator: 0,
            },
          },
        ],
      },
    },
  },
};
for (const { width, locale } of [
  { width: 1440, locale: "en" },
  { width: 1024, locale: "en" },
  { width: 390, locale: "en" },
  { width: 390, locale: "zh" },
]) {
  test(`native analysis layout ${width} ${locale}`, async ({
    page,
    context,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await context.addCookies([
      { name: "NEXT_LOCALE", value: locale, url: "http://127.0.0.1:3184" },
    ]);
    await page.emulateMedia({ reducedMotion: "reduce" });
    const suffix = locale === "en" ? "" : "-zh";
    await context.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let data: unknown = [];
      if (path.endsWith("/auth/me"))
        data = {
          id: "native-user",
          username: "native",
          display_name: "Native UI fixture",
          email: "native@example.invalid",
          global_role: "admin",
          status: "active",
        };
      else if (path.endsWith("/notifications"))
        data = { notifications: [], unread_count: 0 };
      else if (path.endsWith("/capabilities"))
        data = { grants: ["execution.read"], items: {} };
      else if (path.endsWith("/execution-analysis/summary")) data = summary;
      else if (path.endsWith("/execution-analysis/runs"))
        data = {
          watermark: "fixed-capture",
          availability: "available",
          next_cursor: null,
          items: [
            {
              run_id: "run-zero",
              status: "completed",
              family: "agent",
              admitted_at: time,
            },
          ],
        };
      else if (path.endsWith("/execution-analysis/preferences"))
        data = { revision: 0, timezone: null };
      await route.fulfill({ json: { code: 200, msg: "ok", data } });
    });
    await page.goto(
      `/analysis?${new URLSearchParams({ filters: JSON.stringify({ start: time, end: "2026-09-09T00:00:00Z" }), timezone: "UTC", grain: "day" })}`,
    );
    await expect(page.locator("main h1")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "run-zero", exact: true }).first(),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/analysis-${width}${suffix}-light.png`,
      fullPage: true,
    });
    await page
      .getByRole("heading", {
        name: locale === "zh" ? "工具错误" : "Tool errors",
      })
      .scrollIntoViewIfNeeded();
    await expect(
      page.locator(".recharts-bar-rectangle path").first(),
    ).toBeVisible();
    await page.screenshot({
      path: `../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/analysis-${width}${suffix}-lower-light.png`,
    });
    await page.locator("main h1").scrollIntoViewIfNeeded();
    await page.evaluate(() => document.documentElement.classList.add("dark"));
    await page.screenshot({
      path: `../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/analysis-${width}${suffix}-dark.png`,
      fullPage: true,
    });
    await page
      .getByRole("heading", {
        name: locale === "zh" ? "此快照中的运行" : "Runs in this capture",
      })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/analysis-${width}${suffix}-runs-export.png`,
    });
    if (width === 1440) {
      await page.locator("main h1").scrollIntoViewIfNeeded();
      await page.evaluate(() => {
        document.documentElement.style.filter = "grayscale(1)";
      });
      await page.screenshot({
        path: "../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/analysis-1440-grayscale.png",
      });
      await page.evaluate(() => {
        document.documentElement.style.filter = "";
        document.documentElement.style.zoom = "2";
      });
      await page
        .getByRole("button", { name: "Apply filters", exact: true })
        .focus();
      await page.keyboard.press("Tab");
      expect(await page.evaluate(() => document.activeElement?.tagName)).toBe(
        "SUMMARY",
      );
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBe(true);
      await page.screenshot({
        path: "../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/analysis-1440-200percent-keyboard.png",
      });
    }
  });
}

test("comparison owns at most two traces, retained matrix and revocation clears body", async ({
  page,
  context,
}) => {
  const comparisonId = "4f4b846b-fb89-40a6-9092-b987d43c3d67";
  const runs = ["run-one", "run-two", "run-three", "run-four", "run-five"];
  const rows = Array.from({ length: 15 }, (_, i) => ({
    id: `result-${i}`,
    case_id: `case-${i}`,
    case_label: `Case ${i} with a long descriptive label`,
    config_id: `config-${i % 2}`,
    config_label: `Configuration ${i % 2}`,
    repetition: 0,
    attempt: 1,
    run_id: runs[i % 5],
    run_revision: 2,
    result_revision: 3,
    attempts: [],
    score_run_id: runs[i % 5],
    score_run_revision: 2,
    score_result_revision: 3,
    execution_status: "succeeded",
    scoring_status: "complete",
    value: i === 0 ? null : i % 5,
    invalidated: false,
    subject_usage: {
      calls: 1,
      token_known: 1,
      money_known: 1,
      tokens: 10,
      money: i === 2 ? "0" : "0.20",
      unresolved: 0,
    },
    judge_usage: {
      calls: 1,
      token_known: 1,
      money_known: 1,
      tokens: 4,
      money: i === 2 ? "0" : "0.03",
      unresolved: 0,
    },
  }));
  const evaluation = {
    identity: [
      "agent",
      "dataset",
      "live",
      "environment",
      "rubric",
      "metric",
      "human",
      "quality",
      ["quality"],
    ],
    batch_id: "batch",
    evaluation_revision: 3,
    source: "human",
    dimension: "quality",
    rubric_id: "rubric",
    captured_at: "2026-09-09T00:00:00Z",
    usage_watermark: "2026-09-09T00:00:01Z",
    cost_basis: "case_result_subject_plus_judge",
    rows,
    points: rows.map((r, i) => ({
      result_id: r.id,
      case_id: r.case_id,
      config_id: r.config_id,
      excluded: false,
      value: r.value,
      cost_usd: i === 2 ? "0" : "0.23",
    })),
    distribution_metadata: {
      sample_count: 14,
      missing_count: 1,
      excluded_count: 0,
      grain: "case_config",
      timezone: "UTC",
      watermark: time,
      metric_version: "evaluation-series-v1",
    },
    quality_cost_metadata: {
      sample_count: 14,
      missing_count: 1,
      excluded_count: 0,
      grain: "case_result",
      timezone: "UTC",
      watermark: time,
      metric_version: "evaluation-series-v1",
    },
    scoring_counts: { complete: 15 },
    subject_usage: null,
    judge_usage: null,
  };
  let revoked = false;
  const bodyRequests: URL[] = [];
  let diffSelection: Record<string, unknown> | undefined;
  const diffDocument = JSON.stringify({
    format: "text",
    left: { artifact_id: "artifact-run-one", version: 1 },
    right: { artifact_id: "artifact-run-two", version: 1 },
    diff: {
      complete: false,
      content_changed: null,
      reason: "input_limit",
      content: "retained partial artifact diff",
      operations: [],
    },
  });
  await context.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let data: unknown = [];
    if (path.endsWith("/auth/me"))
      data = {
        id: "native-user",
        username: "native",
        display_name: "Native UI fixture",
        email: "native@example.invalid",
        global_role: "admin",
        status: "active",
      };
    else if (path.endsWith("/notifications"))
      data = { notifications: [], unread_count: 0 };
    else if (path.endsWith("/capabilities"))
      data = { grants: ["execution.read"], items: {} };
    else if (path.endsWith("/artifact-diffs")) {
      diffSelection = route.request().postDataJSON();
      data = { job_id: "fixed-diff", status: "queued" };
    } else if (path.endsWith("/diff-jobs/fixed-diff"))
      data = {
        status: "partial",
        content: url.searchParams.has("cursor")
          ? diffDocument.slice(40)
          : diffDocument.slice(0, 40),
        next_cursor: url.searchParams.has("cursor") ? null : "next-diff-page",
      };
    else if (path.endsWith("/body")) {
      bodyRequests.push(url);
      data = {
        availability: "available",
        content: "Retained safe body at fixed revision",
        content_type: "text/plain",
        truncated: false,
        redacted: false,
        digest: "public-digest",
        at: null,
        next_cursor: null,
      };
    } else if (path.endsWith(`/execution-comparisons/${comparisonId}`)) {
      data = {
        comparison_id: comparisonId,
        revision: 1,
        alignment_revision: 0,
        captured_at: "2026-09-09T00:00:00Z",
        timezone: "UTC",
        metric_version: "execution-metrics-v1",
        coverage_changed: revoked,
        member_count: revoked ? 4 : 5,
        baseline_configuration: "config-0",
        members: runs
          .filter((r) => !revoked || r !== "run-one")
          .map((run_id) => ({
            run_id,
            status: "completed",
            family: "agent",
            cut: {},
          })),
        context: {
          start: time,
          end: "2026-09-09T00:00:00Z",
          timezone: "UTC",
          grain: "day",
          filters: {},
          selection_mode: "explicit",
          detail_run_ids: revoked ? [] : runs,
        },
        next_cursor: null,
        alignments: [],
        suggestions: [],
        metrics: {
          ...summary.metrics,
          evaluation_series: [
            revoked
              ? {
                  ...evaluation,
                  points: evaluation.points.map((p) => ({
                    ...p,
                    cost_usd: null,
                  })),
                  rows: rows.map((r) => ({
                    ...r,
                    subject_usage: null,
                    judge_usage: null,
                  })),
                }
              : evaluation,
          ],
        },
        details: revoked
          ? []
          : url.searchParams.getAll("detail_run_ids").map((run_id) => ({
              run_id,
              availability: "available",
              body: {
                steps: [
                  {
                    run_id,
                    step_id: `${run_id}-step`,
                    attempt_id: "attempt-1",
                    kind: "tool",
                    tool_name: "fixed_tool",
                    status: "completed",
                    started_at: time,
                    ended_at: "2026-09-01T00:00:01Z",
                    duration_ms: 1000,
                    projection_revision: 1,
                    completeness: { state: "complete" },
                    output_ref: {
                      availability: "available",
                      content_id: "retained-output",
                    },
                    artifact_refs: [
                      {
                        artifact_id: `artifact-${run_id}`,
                        version: 1,
                        kind: "markdown",
                        availability: "available",
                      },
                    ],
                  },
                ],
              },
            })),
      };
    }
    await route.fulfill({ json: { code: 200, msg: "ok", data } });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/analysis/comparisons/${comparisonId}?revision=1`);
  await expect(
    page.getByRole("heading", { name: "Score distribution" }),
  ).toBeVisible();
  await page
    .getByRole("heading", { name: "Score distribution" })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/comparison-evaluation-1440.png",
  });
  await page
    .locator('section[aria-label="Result matrix"]')
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/comparison-matrix-1440.png",
  });
  await page
    .getByRole("combobox", { name: "Left trace", exact: true })
    .selectOption("run-one");
  await page
    .getByRole("combobox", { name: "Right trace", exact: true })
    .selectOption("run-two");
  await expect(page.locator("[data-full-trace-owner]")).toHaveCount(2);
  await page.locator('[data-step="run-one-step"]').click();
  await page.locator('[data-step="run-two-step"]').click();
  await expect(page.locator("[data-retained-body-owner]")).toHaveCount(2);
  await page
    .getByRole("button", { name: "Load output", exact: true })
    .first()
    .click();
  await expect(
    page.getByText("Retained safe body at fixed revision"),
  ).toBeVisible();
  expect(bodyRequests[0].searchParams.get("revision")).toBe("1");
  expect(bodyRequests[0].searchParams.has("at")).toBe(false);
  await page
    .getByRole("button", { name: "Compare fixed artifacts", exact: true })
    .click();
  await expect(
    page.getByText("retained partial artifact diff", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      /Partial or metadata-only comparison; absence of a diff does not establish equality/,
    ),
  ).toBeVisible();
  expect(diffSelection).toMatchObject({
    revision: 1,
    left: { artifact_id: "artifact-run-one", version: 1 },
    right: { artifact_id: "artifact-run-two", version: 1 },
  });
  await page
    .locator("[data-full-trace-owner]")
    .first()
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/comparison-slots-1440.png",
  });
  await page.setViewportSize({ width: 390, height: 900 });
  await page
    .locator("[data-full-trace-owner]")
    .first()
    .scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/comparison-slots-390.png",
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page
    .getByRole("combobox", { name: "Right trace", exact: true })
    .selectOption("run-three");
  await expect(page.locator("[data-full-trace-owner]")).toHaveCount(2);
  await expect(
    page.getByText("Retained safe body at fixed revision"),
  ).toHaveCount(0);
  await expect(
    page.getByText("retained partial artifact diff", { exact: true }),
  ).toHaveCount(0);
  revoked = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.locator("[data-full-trace-owner]")).toHaveCount(0);
  await expect(page.locator("[data-retained-body-owner]")).toHaveCount(0);
  await expect(
    page.getByText("Retained safe body at fixed revision"),
  ).toHaveCount(0);
});

test("fixed paging and all-matching exclusions keep the capture; filter export accepts a new range snapshot and expiry is explicit", async ({
  page,
  context,
}) => {
  const pageQueries: URL[] = [];
  let exportPayload: Record<string, unknown> | undefined;
  await context.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let data: unknown = [];
    if (path.endsWith("/auth/me"))
      data = {
        id: "native-user",
        username: "native",
        global_role: "admin",
        status: "active",
      };
    else if (path.endsWith("/notifications"))
      data = { notifications: [], unread_count: 0 };
    else if (path.endsWith("/capabilities"))
      data = { grants: ["execution.read"], items: {} };
    else if (path.endsWith("/execution-analysis/summary")) data = summary;
    else if (path.endsWith("/execution-analysis/runs")) {
      pageQueries.push(url);
      const next = url.searchParams.has("cursor");
      data = {
        watermark: "fixed-capture",
        availability: "available",
        next_cursor: next ? null : "opaque-next",
        items: [
          {
            run_id: next ? "run-next" : "run-zero",
            status: "completed",
            family: "agent",
            admitted_at: time,
          },
        ],
      };
    } else if (path.endsWith("/execution-analysis/preferences"))
      data = { revision: 0, timezone: null };
    else if (
      path.endsWith("/execution-analysis/exports") &&
      route.request().method() === "POST"
    ) {
      exportPayload = route.request().postDataJSON();
      data = {
        id: "fixed-export",
        status: "ready",
        created_at: time,
        expires_at: time,
      };
    } else if (path.endsWith("/execution-analysis/exports/fixed-export")) {
      await route.fulfill({
        status: 410,
        json: {
          code: 410,
          msg: "export_expired",
          data: { code: "export_expired" },
        },
      });
      return;
    }
    await route.fulfill({ json: { code: 200, msg: "ok", data } });
  });
  await page.goto(
    `/analysis?${new URLSearchParams({ filters: JSON.stringify({ start: time, end: "2026-09-09T00:00:00Z" }), timezone: "UTC", grain: "day" })}`,
  );
  await page
    .getByRole("button", { name: "Select current page", exact: true })
    .click();
  await expect(
    page.getByRole("checkbox", { name: "Selected run-zero", exact: true }),
  ).toBeChecked();
  await page
    .getByRole("button", { name: "Select all matching on server", exact: true })
    .click();
  await page
    .getByRole("checkbox", { name: "Selected run-zero", exact: true })
    .uncheck();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(
    page.getByRole("checkbox", { name: "Selected run-next", exact: true }),
  ).toBeChecked();
  expect(pageQueries.at(-1)?.searchParams.get("watermark")).toBe(
    "fixed-capture",
  );
  expect(pageQueries.at(-1)?.searchParams.get("cursor")).toBe("opaque-next");
  await page
    .getByRole("button", { name: "Create export snapshot (CSV)", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Download", exact: true }),
  ).toBeVisible();
  expect(exportPayload).toMatchObject({
    source_kind: "filter",
    selection: {
      mode: "all_matching",
      excluded_run_ids: ["run-zero"],
      timezone: "UTC",
      filters: { start: time, end: "2026-09-09T00:00:00Z" },
    },
  });
  expect(exportPayload).not.toHaveProperty("watermark");
  await page.getByRole("button", { name: "Download", exact: true }).click();
  await expect(page.locator("main").getByRole("alert")).toHaveText(
    "This export has expired. Create a new export snapshot.",
  );
});

test("five configuration strata stay bounded and sparse/null rows stay inspectable", async ({
  page,
  context,
}) => {
  await context.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = [];
    if (path.endsWith("/auth/me"))
      data = {
        id: "native-user",
        username: "native",
        global_role: "admin",
        status: "active",
      };
    else if (path.endsWith("/notifications"))
      data = { notifications: [], unread_count: 0 };
    else if (path.endsWith("/capabilities"))
      data = { grants: ["execution.read"], items: {} };
    else if (path.endsWith("/execution-analysis/summary"))
      data = {
        ...summary,
        metrics: {
          ...summary.metrics,
          series: Array.from({ length: 6 }, (_, index) =>
            summary.metrics.series.slice(0, index === 4 ? 3 : 8).map((row) => ({
              ...row,
              group: {
                ...row.group,
                configuration_revision: `config-stratum-${index}`,
              },
            })),
          ).flat(),
        },
      };
    else if (path.endsWith("/execution-analysis/runs"))
      data = {
        watermark: "fixed-capture",
        availability: "available",
        next_cursor: null,
        items: [],
      };
    else if (path.endsWith("/execution-analysis/preferences"))
      data = { revision: 0, timezone: null };
    await route.fulfill({ json: { code: 200, msg: "ok", data } });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(
    `/analysis?${new URLSearchParams({ filters: JSON.stringify({ start: time, end: "2026-09-09T00:00:00Z" }), timezone: "UTC", grain: "day" })}`,
  );
  await expect(
    page.getByRole("img", { name: "Success rate (%)", exact: true }),
  ).toHaveCount(5);
  await expect(
    page
      .locator("p")
      .filter({ hasText: /^config-stratum-4/ })
      .first(),
  ).toBeVisible();
  await expect(
    page.locator("p").filter({ hasText: /^config-stratum-5/ }),
  ).toHaveCount(0);
  await page.locator("main h1").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/analysis-five-configurations-1440.png",
  });
});

for (const selectionSize of ["small", "explicit", "all_matching"] as const) {
  test(`fixround2 ${selectionSize} actual later-page Run to session redirect and Return preserves bounded context`, async ({
    page,
    context,
  }) => {
    await context.addCookies([
      { name: "NEXT_LOCALE", value: "en", domain: "127.0.0.1", path: "/" },
    ]);
    const browserErrors: string[] = [];
    page.on("pageerror", (error) => browserErrors.push(error.message));
    let acceptedStart = time;
    const reads: { path: string; query: URLSearchParams }[] = [];
    const run = {
      run_id: "run-second",
      public_summary: "Return path fixture",
      projection_revision: 1,
      latest_available: "run-cut",
      as_of: time,
      capabilities: [],
      completeness: {
        state: "complete",
        missing_fields: [],
        missing_intervals: [],
      },
      family: "agent",
      purpose: "interactive",
      schema_version: 1,
      scope: { owner_user_id: "native-user" },
      source: {
        entity_type: "session",
        entity_id: "session-return",
        session_id: "session-return",
      },
      status: "completed",
      wait_reason: null,
    };
    await context.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      let data: unknown = [];
      if (path.endsWith("/auth/me"))
        data = {
          id: "native-user",
          username: "native",
          display_name: "Native fixture",
          email: "native@example.invalid",
          global_role: "user",
          status: "active",
        };
      else if (path.endsWith("/capabilities"))
        data = { grants: ["execution.read"], items: {} };
      else if (path.endsWith("/notifications"))
        data = { notifications: [], unread_count: 0 };
      else if (path.endsWith("/execution-analysis/summary")) {
        reads.push({ path, query: url.searchParams });
        const accepted = JSON.parse(url.searchParams.get("filters")!);
        acceptedStart = accepted.start;
        data = {
          ...summary,
          metrics: {
            ...summary.metrics,
            captured_at: accepted.end,
            series: summary.metrics.series.map((entry, index) => ({
              ...entry,
              group: {
                ...entry.group,
                bucket: new Date(
                  Date.parse(accepted.start) + index * 86400000,
                ).toISOString(),
              },
            })),
          },
        };
      } else if (path.endsWith("/execution-analysis/runs")) {
        reads.push({ path, query: url.searchParams });
        const second = url.searchParams.get("cursor") === "opaque-page-two";
        data = {
          watermark: summary.watermark,
          availability: "available",
          next_cursor: second ? null : "opaque-page-two",
          items: [
            {
              run_id: second ? "run-second" : "run-first",
              status: "completed",
              family: "agent",
              admitted_at: acceptedStart,
            },
          ],
        };
      } else if (path.endsWith("/execution-analysis/preferences"))
        data = { revision: 0, timezone: null };
      else if (path.endsWith("/execution-runs/run-second/view"))
        data = {
          at: "run-cut",
          revision: 1,
          hidden_count: 0,
          next_cursor: null,
          steps: [],
          run,
        };
      else if (path.endsWith("/execution-runs/run-second/timeline"))
        data = {
          run_id: "run-second",
          revision: 1,
          at: "run-cut",
          buckets: [],
          key_events: [],
          latest_available: "run-cut",
          completeness: run.completeness,
        };
      else if (path.endsWith("/execution-runs/run-second/events"))
        data = {
          events: [],
          has_earlier: false,
          prev_cursor: null,
          next_cursor: null,
        };
      else if (path.endsWith("/execution-runs"))
        data = { items: [run], next_cursor: null };
      else if (path.endsWith("/artifacts")) data = { artifacts: [] };
      else if (path.endsWith("/sessions/session-return"))
        data = {
          id: "session-return",
          title: "Return path session",
          status: "completed",
          events: [],
          messages: [],
          created_at: time,
          updated_at: time,
        };
      else if (path.endsWith("/sessions/session-return/events"))
        data = { events: [], has_more: false, next_cursor: null };
      else if (path.endsWith("/sessions"))
        data = { sessions: [], has_more: false };
      else if (path.endsWith("/skills")) data = { skills: [] };
      else if (path.endsWith("/inference/models")) data = { models: [] };
      await route.fulfill({ json: { code: 200, msg: "ok", data } });
    });
    await page.goto("/analysis");
    if (selectionSize !== "small") {
      // Execute the real production codec in the isolated browser, using existing TypeScript.
      // No application endpoint or test-only production hook is added.
      const compile = (file: string) =>
        ts.transpileModule(
          readFileSync(
            path.resolve(`../ui/src/lib/analysis-view/${file}.ts`),
            "utf8",
          ),
          {
            compilerOptions: {
              module: ts.ModuleKind.CommonJS,
              target: ts.ScriptTarget.ES2020,
            },
          },
        ).outputText;
      await page.addScriptTag({
        content: `(() => { const modules = {}; const selection = {}; ((exports) => {${compile("selection")}})(selection); const context = {}; ((exports, require) => {${compile("navigation-context")}})(context, () => selection); window.analysisContextTest = context; })()`,
      });
      const href = await page.evaluate((mode) => {
        const codec = (
          window as unknown as { analysisContextTest: typeof ContextCodec }
        ).analysisContextTest;
        const ids = Array.from(
          { length: 100_000 },
          (_, i) =>
            `00000000-0000-4000-8000-${i.toString(16).padStart(12, "0")}`,
        );
        const end = new Date();
        const params = new URLSearchParams({
          filters: JSON.stringify({
            start: new Date(end.getTime() - 7 * 86400000).toISOString(),
            end: end.toISOString(),
          }),
          grain: "day",
          timezone: "UTC",
          watermark: "fixed-capture",
        });
        const selection = {
          mode,
          runIds: mode === "explicit" ? ids : [],
          excludedIds: mode === "all_matching" ? ids : [],
          details: [],
        };
        const owner = JSON.stringify(["native-user", ""]);
        const href = codec.saveNavigationContext(
          { path: "/analysis", params, selection },
          owner,
        );
        const restored = codec.readNavigationContext(
          "/analysis",
          new URL(href, location.origin).searchParams,
          owner,
        )!;
        const actual =
          mode === "explicit"
            ? restored.selection!.runIds
            : restored.selection!.excludedIds;
        if (actual.length !== 100_000 || actual.some((id, i) => id !== ids[i]))
          throw new Error("inexact native DOM-storage roundtrip");
        return href;
      }, selectionSize);
      expect(href.length).toBeLessThan(150);
      await page.goto(href);
    }
    await expect(
      page.getByRole("button", { name: "run-first", exact: true }),
    ).toBeVisible();
    if (selectionSize === "small")
      await page
        .getByRole("button", {
          name: "Select all matching on server",
          exact: true,
        })
        .click();
    await page.getByRole("button", { name: "Next", exact: true }).click();
    expect(page.url().length).toBeLessThan(200);
    const row = page.getByRole("button", { name: "run-second", exact: true });
    await expect(row).toBeVisible();
    if (selectionSize === "small")
      await page
        .getByRole("checkbox", { name: "Selected run-second", exact: true })
        .uncheck();
    await row.click();
    await expect(page).toHaveURL(/\/sessions\/session-return\?/);
    const destination = new URL(page.url()).searchParams.get(
      "analysis_return",
    )!;
    const saved = new URL(destination, "http://127.0.0.1:3184");
    expect(page.url().length).toBeLessThan(350);
    expect(destination.length).toBeLessThan(150);
    expect(saved.searchParams.has("selection")).toBe(false);
    const savedEnvelope = await page.evaluate(
      (locator) =>
        JSON.parse(
          sessionStorage.getItem(`opencitadel:analysis-return:v1:${locator}`)!,
        ),
      saved.searchParams.get("context"),
    );
    const savedParams = new URLSearchParams(savedEnvelope.params);
    expect(savedParams.get("watermark")).toBe(summary.watermark);
    expect(savedParams.get("cursor")).toBe("opaque-page-two");
    expect(JSON.parse(savedParams.get("filters")!).start).toBeTruthy();
    const back = page.getByRole("link", {
      name: "Return to analysis",
      exact: true,
    });
    await expect(back).toHaveAttribute("href", destination);
    await back.click();
    await expect(
      page.getByRole("button", { name: "run-second", exact: true }),
    ).toBeVisible();
    expect(
      reads
        .filter((r) => r.path.endsWith("/runs"))
        .at(-1)!
        .query.get("cursor"),
    ).toBe("opaque-page-two");
    expect(
      reads
        .filter((r) => r.path.endsWith("/summary"))
        .at(-1)!
        .query.get("watermark"),
    ).toBe(summary.watermark);
    if (selectionSize === "all_matching")
      await expect(
        page.getByRole("checkbox", {
          name: "Selected run-second",
          exact: true,
        }),
      ).toBeChecked();
    else
      await expect(
        page.getByRole("checkbox", {
          name: "Selected run-second",
          exact: true,
        }),
      ).not.toBeChecked();
    if (selectionSize !== "small") {
      const compile = (file: string) =>
        ts.transpileModule(
          readFileSync(
            path.resolve(`../ui/src/lib/analysis-view/${file}.ts`),
            "utf8",
          ),
          {
            compilerOptions: {
              module: ts.ModuleKind.CommonJS,
              target: ts.ScriptTarget.ES2020,
            },
          },
        ).outputText;
      await page.addScriptTag({
        content: `(() => { const selection = {}; ((exports) => {${compile("selection")}})(selection); const context = {}; ((exports, require) => {${compile("navigation-context")}})(context, () => selection); window.analysisContextTest = context; })()`,
      });
      expect(
        await page.evaluate((mode) => {
          const codec = (
            window as unknown as { analysisContextTest: typeof ContextCodec }
          ).analysisContextTest;
          const restored = codec.readNavigationContext(
            "/analysis",
            new URL(location.href).searchParams,
            JSON.stringify(["native-user", ""]),
          )!;
          const ids =
            mode === "explicit"
              ? restored.selection!.runIds
              : restored.selection!.excludedIds;
          return (
            ids.length === 100_000 &&
            ids.every(
              (id, i) =>
                id ===
                `00000000-0000-4000-8000-${i.toString(16).padStart(12, "0")}`,
            )
          );
        }, selectionSize),
      ).toBe(true);
    }
    await expect(
      page.getByRole("button", { name: "run-second", exact: true }),
    ).toBeFocused();
    expect(new URL(page.url()).hash).toBe("#run-run-second");
    await expect
      .poll(() =>
        page.locator("main").evaluate((el) => el.parentElement!.scrollTop),
      )
      .toBe(Number(savedParams.get("scroll")));
    expect(browserErrors).toEqual([]);
    await page.screenshot({
      path: `../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-fix2-browser-return-${selectionSize}.png`,
    });
  });
}
