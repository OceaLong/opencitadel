import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";

export const ACCEPTANCE_PROJECTS = [
  "bootstrap",
  "identity",
  "control-plane",
  "resources",
  "execution",
  "patrol-admin",
  "cleanup",
] as const;

type AcceptanceProject = (typeof ACCEPTANCE_PROJECTS)[number];
type PlaywrightStatus = TestResult["status"] | "not_run";
type CoverageStatus = "passed" | "failed" | "skipped" | "not_run";

export type AcceptanceRecord = {
  requirementId: string;
  testId: string;
  project: string;
  status: PlaywrightStatus;
  durationMs: number;
};

export type AcceptanceEvaluation = {
  errors: string[];
  coverage: Array<{
    requirement_id: string;
    test_id: string;
    project: string;
    status: CoverageStatus;
  }>;
};

type EvidenceProject = {
  name: AcceptanceProject;
  tests: number;
  passed: number;
  failed: number;
  skipped: number;
  duration_ms: number;
};

function coverageStatus(status: PlaywrightStatus): CoverageStatus {
  if (status === "passed" || status === "skipped" || status === "not_run") {
    return status;
  }
  return "failed";
}

export function evaluateAcceptance(
  records: readonly AcceptanceRecord[],
  requiredIds: readonly string[],
): AcceptanceEvaluation {
  const required = new Set(requiredIds);
  const counts = new Map<string, number>();
  const errors: string[] = [];

  for (const record of records) {
    if (!required.has(record.requirementId)) {
      errors.push(`unknown acceptance requirement ${record.requirementId}`);
      continue;
    }
    const owner = requirementProject(record.requirementId);
    if (record.project !== owner) {
      errors.push(
        `acceptance requirement ${record.requirementId} belongs to ${owner}, not ${record.project}`,
      );
    }
    counts.set(record.requirementId, (counts.get(record.requirementId) ?? 0) + 1);
    if (record.status !== "passed") {
      errors.push(`acceptance test ${record.testId} was ${record.status}`);
    }
  }

  for (const requirementId of requiredIds) {
    const count = counts.get(requirementId) ?? 0;
    if (count === 0) {
      errors.push(`acceptance requirement ${requirementId} is missing`);
    } else if (count !== 1) {
      errors.push(`acceptance requirement ${requirementId} is covered ${count} times`);
    }
  }

  return {
    errors,
    coverage: records
      .filter((record) => required.has(record.requirementId))
      .map((record) => ({
        requirement_id: record.requirementId,
        test_id: record.testId,
        project: record.project,
        status: coverageStatus(record.status),
      }))
      .sort((left, right) => left.requirement_id.localeCompare(right.requirement_id)),
  };
}

function loadRequiredIds(): string[] {
  const schemaPath = resolve(
    __dirname,
    "../../contracts/acceptance-evidence.schema.json",
  );
  const schema = JSON.parse(readFileSync(schemaPath, "utf8")) as {
    $defs?: { requirementId?: { enum?: unknown } };
  };
  const values = schema.$defs?.requirementId?.enum;
  if (!Array.isArray(values) || values.some((value) => typeof value !== "string")) {
    throw new Error("acceptance evidence schema has no string requirement enum");
  }
  return values as string[];
}

function requirementProject(requirementId: string): AcceptanceProject {
  if (requirementId.startsWith("ID-")) return "identity";
  if (requirementId.startsWith("INF-") || requirementId.startsWith("POL-")) {
    return "control-plane";
  }
  if (requirementId.startsWith("KB-") || requirementId.startsWith("CB-")) {
    return "resources";
  }
  if (requirementId.startsWith("RUN-")) return "execution";
  if (
    requirementId.startsWith("PAT-") ||
    requirementId.startsWith("ADM-") ||
    requirementId.startsWith("UI-")
  ) {
    return "patrol-admin";
  }
  throw new Error(`acceptance requirement has no owning project: ${requirementId}`);
}

function selectedProjects(): Set<string> {
  const value = process.env.ACCEPTANCE_PLAYWRIGHT_PROJECTS;
  if (!value) return new Set(ACCEPTANCE_PROJECTS);
  const projects = new Set(value.split(",").filter(Boolean));
  for (const project of projects) {
    if (!(ACCEPTANCE_PROJECTS as readonly string[]).includes(project)) {
      throw new Error(`unknown selected acceptance project: ${project}`);
    }
  }
  return projects;
}

function finalResult(test: TestCase): TestResult | undefined {
  return test.results.at(-1);
}

function projectName(test: TestCase): string {
  return test.parent.project()?.name ?? "unknown";
}

function recordFor(test: TestCase, result: TestResult): AcceptanceRecord[] {
  return result.annotations
    .filter((annotation) => annotation.type === "acceptance" && annotation.description)
    .map((annotation) => ({
      requirementId: annotation.description as string,
      testId: test.id,
      project: projectName(test),
      status: result.status,
      durationMs: result.duration,
    }));
}

function projectEvidence(tests: readonly TestCase[]): EvidenceProject[] {
  return ACCEPTANCE_PROJECTS.map((name) => {
    const results = tests
      .filter((test) => projectName(test) === name)
      .map(finalResult)
      .filter((result): result is TestResult => result !== undefined);
    return {
      name,
      tests: results.length,
      passed: results.filter((result) => result.status === "passed").length,
      failed: results.filter((result) =>
        ["failed", "timedOut", "interrupted"].includes(result.status),
      ).length,
      skipped: results.filter((result) => result.status === "skipped").length,
      duration_ms: results.reduce((total, result) => total + result.duration, 0),
    };
  });
}

function writeJsonAtomic(path: string, value: unknown): void {
  mkdirSync(dirname(path), { recursive: true });
  const temporary = `${path}.tmp-${process.pid}`;
  writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  renameSync(temporary, path);
}

export default class ZeroSkipReporter implements Reporter {
  private suite?: Suite;

  onBegin(_config: FullConfig, suite: Suite): void {
    this.suite = suite;
  }

  async onEnd(
    result: FullResult,
  ): Promise<{ status?: FullResult["status"] } | undefined> {
    const tests = this.suite?.allTests() ?? [];
    const records = tests.flatMap((test) => {
      const final = finalResult(test);
      return final ? recordFor(test, final) : [];
    });
    const selected = selectedProjects();
    const requiredIds = loadRequiredIds().filter((requirementId) =>
      selected.has(requirementProject(requirementId)),
    );
    const evaluation = evaluateAcceptance(records, requiredIds);

    for (const test of tests) {
      const status = finalResult(test)?.status ?? "not_run";
      if (status === "skipped" || status === "interrupted") {
        const message = `Playwright test ${test.id} was ${status}`;
        if (!evaluation.errors.includes(message)) evaluation.errors.push(message);
      }
    }

    const evidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR;
    if (evidenceDir) {
      writeJsonAtomic(resolve(evidenceDir, "playwright/results.json"), {
        projects: projectEvidence(tests),
        coverage: evaluation.coverage,
        errors: evaluation.errors,
      });
    }

    const strict = Boolean(process.env.ACCEPTANCE_RUN_ID);
    if (strict && evaluation.errors.length > 0) {
      process.stderr.write(
        `\nAcceptance coverage failed:\n${evaluation.errors.map((error) => `- ${error}`).join("\n")}\n`,
      );
      return { status: "failed" };
    }
    return result.status === "passed" ? undefined : { status: result.status };
  }

  printsToStdio(): boolean {
    return false;
  }
}
