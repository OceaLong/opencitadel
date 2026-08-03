// @vitest-environment jsdom

import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { PatrolRunDetail } from "@/lib/api/types";

import en from "../../../messages/en.json";
import zh from "../../../messages/zh.json";

const mocks = vi.hoisted(() => ({
  getMCPServers: vi.fn().mockResolvedValue({ mcp_servers: [] }),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/config", () => ({ configApi: { getMCPServers: mocks.getMCPServers } }));
vi.mock("@/lib/api/patrols", () => ({
  patrolsApi: { downloadEvidence: vi.fn(), decideFinding: vi.fn() },
}));

import { PackWizard } from "./pack-wizard";
import { PatrolRunDetailView } from "./patrol-run-detail";

const reactGlobals = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean };

beforeAll(() => {
  reactGlobals.IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  document.body.replaceChildren();
});

function runFixture(): PatrolRunDetail {
  return {
    id: "run-1",
    pack_id: "pack-1",
    pack_version: 1,
    session_id: "session-1",
    status: "completed_with_findings",
    trigger_type: "manual",
    started_at: "2026-08-03T00:00:00Z",
    finished_at: "2026-08-03T00:01:00Z",
    duration_ms: 60_000,
    counts: { pass: 9, warn: 1, fail: 0, error: 0, skipped: 0 },
    evidence_completeness: 1,
    summary: {},
    created_at: "2026-08-03T00:00:00Z",
    check_results: [],
    findings: [
      {
        id: "finding-1",
        run_id: "run-1",
        check_result_id: "result-1",
        fingerprint: "f".repeat(64),
        severity: "warning",
        status: "open",
        title: "Restart spike",
        summary: "Four restarts",
        first_seen_at: "2026-08-03T00:01:00Z",
        last_seen_at: "2026-08-03T00:01:00Z",
        occurrence_count: 1,
      },
    ],
  };
}

describe.each([
  ["en", en],
  ["zh", zh],
] as const)("Patrol UI %s", (locale, messages) => {
  it("renders the localized wizard without raw query or URL controls", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <NextIntlClientProvider locale={locale} messages={messages}>
          <PackWizard />
        </NextIntlClientProvider>,
      );
      await Promise.resolve();
    });
    expect(container.textContent).toContain(messages.patrol.wizard.name);
    expect(container.textContent).toContain(messages.patrol.wizard.readOnlyBoundary);
    expect(container.querySelector('input[name="url"]')).toBeNull();
    expect(container.querySelector('textarea[name="promql"]')).toBeNull();
    await act(async () => root.unmount());
  });

  it("keeps auditor Run detail read-only while retaining evidence download", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <NextIntlClientProvider locale={locale} messages={messages}>
          <PatrolRunDetailView run={runFixture()} readOnly onRefresh={() => undefined} />
        </NextIntlClientProvider>,
      );
    });
    expect(container.textContent).toContain(messages.patrol.actions.downloadEvidence);
    expect(container.textContent).not.toContain(messages.patrol.actions.acknowledge);
    expect(container.textContent).not.toContain(messages.patrol.actions.falsePositive);
    await act(async () => root.unmount());
  });
});
