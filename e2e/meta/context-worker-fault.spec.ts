import { expect, test } from "@playwright/test";
import { requireOwnedKernel } from "../support/context-worker-fault";

const project = "oc-owned-context",
  run = "owned-context";
const document = () => ({
  Id: "a".repeat(64),
  Config: {
    Labels: {
      "com.docker.compose.project": project,
      "com.docker.compose.service": "opencitadel-execution-kernel",
      "com.opencitadel.acceptance.project": project,
      "com.opencitadel.acceptance.run": run,
    },
  },
});
test("worker fault rejects another run, project, service or invalid container", () => {
  expect(requireOwnedKernel(document(), project, run)).toBe("a".repeat(64));
  for (const key of Object.keys(document().Config.Labels)) {
    const other = document();
    (other.Config.Labels as Record<string, string>)[key] = "foreign";
    expect(() => requireOwnedKernel(other, project, run)).toThrow(/foreign/);
  }
  expect(() =>
    requireOwnedKernel({ ...document(), Id: "bad" }, project, run),
  ).toThrow();
  expect(() => requireOwnedKernel(document(), "--all", run)).toThrow();
});
