import { expect, test } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

import {
  evaluateAcceptance,
  type AcceptanceRecord,
} from "../reporters/zero-skip-reporter";

const REQUIRED = ["ID-LOGIN", "INF-ENDPOINT"] as const;

test("global timeout retains an unstarted cleanup in totals and errors", () => {
  const directory = mkdtempSync(resolve(tmpdir(), "opencitadel-reporter-"));
  try {
    const playwright = require.resolve("@playwright/test");
    const reporter = resolve(__dirname, "../reporters/zero-skip-reporter.ts");
    for (const [name, body] of Object.entries({
      bootstrap: "async () => {}",
      control:
        "async ({}, info) => { info.annotations.push({type: 'acceptance', description: 'INF-ENDPOINT'}); }",
      execution: "async () => { await new Promise(() => {}); }",
      cleanup: "async () => {}",
    })) {
      writeFileSync(
        resolve(directory, `${name}.spec.js`),
        `const { test } = require(${JSON.stringify(playwright)});\ntest(${JSON.stringify(name)}, ${body});\n`,
      );
    }
    const config = resolve(directory, "playwright.config.js");
    writeFileSync(
      config,
      `module.exports = {
        testDir: ${JSON.stringify(directory)}, workers: 1,
        timeout: 60000, globalTimeout: 3000,
        reporter: [[${JSON.stringify(reporter)}]],
        projects: [
          {name: 'bootstrap', testMatch: /bootstrap\\.spec\\.js/, teardown: 'cleanup'},
          {name: 'control-plane', testMatch: /control\\.spec\\.js/, dependencies: ['bootstrap']},
          {name: 'execution', testMatch: /execution\\.spec\\.js/, dependencies: ['control-plane']},
          {name: 'cleanup', testMatch: /cleanup\\.spec\\.js/}
        ]
      };`,
    );
    const child = spawnSync(
      process.execPath,
      [require.resolve("@playwright/test/cli"), "test", "--config", config],
      {
        timeout: 15000,
        encoding: "utf8",
        env: {
          ...process.env,
          ACCEPTANCE_EVIDENCE_DIR: directory,
          ACCEPTANCE_PLAYWRIGHT_PROJECTS: "execution",
          ACCEPTANCE_RUN_ID: "",
        },
      },
    );
    expect(child.error).toBeUndefined();
    expect(child.status).toBe(1);
    const evidence = JSON.parse(
      readFileSync(resolve(directory, "playwright/results.json"), "utf8"),
    );
    expect(
      evidence.projects.find((p: { name: string }) => p.name === "cleanup"),
    ).toMatchObject({ tests: 1, passed: 0, failed: 0, skipped: 1 });
    expect(
      evidence.errors.some((error: string) => error.endsWith("was not_run")),
    ).toBe(true);
    expect(
      evidence.projects.find(
        (p: { name: string }) => p.name === "control-plane",
      ),
    ).toMatchObject({ tests: 1, passed: 1, failed: 0, skipped: 0 });
    expect(evidence.errors).not.toContain(
      "unknown acceptance requirement INF-ENDPOINT",
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

function record(
  requirementId: string,
  status: AcceptanceRecord["status"] = "passed",
  testId = `test-${requirementId}`,
): AcceptanceRecord {
  return {
    requirementId,
    testId,
    project: requirementId.startsWith("ID-") ? "identity" : "control-plane",
    status,
    durationMs: 1,
  };
}

test("accepts an exact, passing requirement set", () => {
  const result = evaluateAcceptance(
    [record("ID-LOGIN"), record("INF-ENDPOINT")],
    REQUIRED,
  );

  expect(result.errors).toEqual([]);
  expect(result.coverage).toHaveLength(2);
});

for (const status of ["skipped", "interrupted"] as const) {
  test(`rejects a ${status} acceptance result`, () => {
    const result = evaluateAcceptance(
      [record("ID-LOGIN", status), record("INF-ENDPOINT")],
      REQUIRED,
    );

    expect(result.errors).toContain(
      `acceptance test test-ID-LOGIN was ${status}`,
    );
  });
}

test("rejects duplicate requirement IDs", () => {
  const result = evaluateAcceptance(
    [
      record("ID-LOGIN"),
      record("ID-LOGIN", "passed", "duplicate"),
      record("INF-ENDPOINT"),
    ],
    REQUIRED,
  );

  expect(result.errors).toContain(
    "acceptance requirement ID-LOGIN is covered 2 times",
  );
});

test("rejects missing and unknown requirement IDs", () => {
  const result = evaluateAcceptance(
    [record("ID-LOGIN"), record("NOT-A-REQUIREMENT")],
    REQUIRED,
  );

  expect(result.errors).toContain(
    "acceptance requirement INF-ENDPOINT is missing",
  );
  expect(result.errors).toContain(
    "unknown acceptance requirement NOT-A-REQUIREMENT",
  );
});

test("all execution ACs have canonical ownership and exact passing coverage", async () => {
  const { requirementProject } =
    await import("../reporters/zero-skip-reporter");
  const ids = Array.from(
    { length: 22 },
    (_, index) => `AC${String(index + 1).padStart(2, "0")}`,
  );
  for (const id of ids) expect(requirementProject(id)).toBe("execution");
  const records = ids.map((id) => ({ ...record(id), project: "execution" }));
  expect(evaluateAcceptance(records, ids).errors).toEqual([]);
  expect(() => requirementProject("AC23")).toThrow();
  expect(() => requirementProject("AC00")).toThrow();
  for (const status of [
    "failed",
    "timedOut",
    "interrupted",
    "skipped",
    "not_run",
  ] as const) {
    expect(
      evaluateAcceptance([{ ...records[0], status }, ...records.slice(1)], ids)
        .errors,
    ).toContain(`acceptance test test-AC01 was ${status}`);
  }
  expect(evaluateAcceptance(records.slice(1), ids).errors).toContain(
    "acceptance requirement AC01 is missing",
  );
  expect(evaluateAcceptance([...records, records[0]], ids).errors).toContain(
    "acceptance requirement AC01 is covered 2 times",
  );
  expect(
    evaluateAcceptance(
      [{ ...records[0], project: "resources" }, ...records.slice(1)],
      ids,
    ).errors,
  ).toContain(
    "acceptance requirement AC01 belongs to execution, not resources",
  );
});
