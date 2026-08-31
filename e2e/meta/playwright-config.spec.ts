import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import config from "../playwright.config";

test("defines the complete project graph with unconditional teardown", () => {
  const projects = config.projects ?? [];
  expect(projects.map((project) => project.name)).toEqual([
    "bootstrap",
    "identity",
    "control-plane",
    "resources",
    "execution",
    "patrol-admin",
    "cleanup",
  ]);
  expect(
    projects.find((project) => project.name === "bootstrap")?.teardown,
  ).toBe("cleanup");
  expect(
    projects.find((project) => project.name === "cleanup")?.dependencies,
  ).toBeUndefined();
  for (const [name, dependencies] of Object.entries({
    identity: ["bootstrap"],
    "control-plane": ["bootstrap"],
    resources: ["control-plane"],
    execution: ["bootstrap"],
    "patrol-admin": ["control-plane"],
  })) {
    expect(
      projects.find((project) => project.name === name)?.dependencies,
    ).toEqual(dependencies);
  }
});

test("required Patrol coverage has no environment gate or conditional skip", () => {
  const source = readFileSync(
    resolve(__dirname, "../patrol-admin.spec.ts"),
    "utf8",
  );
  expect(source).not.toContain("PATROL_E2E");
  expect(source).not.toContain("test.skip");
});

test("bootstrap raises the acceptance request budget through a reversible policy revision", () => {
  const source = readFileSync(
    resolve(__dirname, "../bootstrap.setup.ts"),
    "utf8",
  );
  expect(source).toContain("registerCleanupAction");
  expect(source).toContain("requests_per_minute: 100_000");
  expect(source).toContain('policy: "operations"');
});
