// @vitest-environment jsdom

import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { ExecutionPolicy, ExecutionPolicyRevision } from "@/lib/api/runtime-policies";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";

const mocks = vi.hoisted(() => ({
  getExecution: vi.fn(),
  getOperations: vi.fn(),
  listExecutionRevisions: vi.fn(),
  listOperationsRevisions: vi.fn(),
  createExecution: vi.fn(),
  createOperations: vi.fn(),
  restoreExecution: vi.fn(),
  restoreOperations: vi.fn(),
}));

vi.mock("@/lib/api/runtime-policies", () => ({ runtimePolicyApi: mocks }));

import { PolicyHistory } from "./runtime-policy/policy-history";
import { RuntimePolicySettings } from "./runtime-policy-settings";

const HEAD = {
  id: "global",
  version: 4,
  execution_revision_id: "11111111-1111-4111-8111-111111111111",
  operations_revision_id: "22222222-2222-4222-8222-222222222222",
  updated_by: "admin-1",
  updated_at: "2026-08-26T04:00:00Z",
};

const EXECUTION_POLICY = {
  agent: { max_iterations: 12, max_retries: 2 },
  model_resilience: {
    enabled: true,
    fallback_enabled: false,
    allow_cross_provider_fallback: false,
    fallback_on_quota_exceeded: true,
    allow_cross_provider_fallback_on_quota: true,
    max_attempts_per_call: 3,
    max_call_budget_seconds: 120,
    breaker_window_seconds: 60,
    breaker_error_threshold: 5,
    breaker_open_ttl_seconds: 60,
    breaker_halfopen_probe_timeout_seconds: 10,
    fast_fail_on_open_circuit: true,
  },
  activity: { tool_timeout_seconds: 120, mcp_connect_timeout_seconds: 30 },
  memory: { recall_limit: 20, vector_enabled: false },
  knowledge_base: {
    vector_enabled: true,
    chunk: { parent_max_chars: 2000, child_max_chars: 400, overlap: 50 },
    retrieval: { vector_top_k: 20, bm25_top_k: 20, rrf_k: 60, final_top_k: 8 },
    rerank: { enabled: true, timeout_seconds: 30 },
    graphrag: {
      enabled: true,
      max_parent_chunks_per_doc: 200,
      concurrency: 3,
      max_chunks: 10000,
      max_llm_calls: 10000,
      max_tokens: 1000000,
      deadline_seconds: 300,
    },
    ocr: { mode: "vision_llm", max_pages: 50 },
    document: { max_bytes: 52428800, max_pages: 1000 },
  },
  codebase: {
    vector_enabled: true,
    analysis: {
      max_file_size_bytes: 512000,
      max_files: 5000,
      chunk_max_chars: 2000,
      source_read_batch_size: 50,
    },
    retrieval: { fetch_multiplier: 3, rrf_k: 60, final_top_k: 8 },
  },
} satisfies ExecutionPolicy;

const OPERATIONS_POLICY = {
  traffic: {
    rate_limit_enabled: true,
    requests_per_minute: 120,
    session_stream_interval_seconds: 15,
  },
  scheduler: {
    enabled: true,
    poll_interval_seconds: 10,
    max_concurrent_jobs: 5,
    leader_lease_seconds: 30,
    webhook_idempotency_ttl_seconds: 600,
  },
  patrol: { admission: "accepting", remediation: "disabled" },
  sandbox: {
    ttl_minutes: 60,
    cleanup_interval_seconds: 300,
    memory_limit: "2g",
    cpu_limit: 2,
    pids_limit: 512,
    pool_enabled: true,
    pool_size: 2,
    idle_timeout_minutes: 30,
    warmup_retry_interval_seconds: 0.5,
    warmup_max_retries: 30,
    max_sandboxes_per_node: 4,
    max_dynamic_sandboxes_global: 0,
    admission_min_host_available_mb: 3072,
    admission_reclaim_target_mb: 4096,
    admission_poll_interval_seconds: 2,
    admission_settle_seconds: 8,
    admission_reclaim_enabled: true,
    reclaim_leader_lease_seconds: 15,
  },
  resource_gc: {
    knowledge_base: { enabled: false, retention_count: 10, retention_min_days: 30, batch_size: 50 },
    codebase: { enabled: false, retention_count: 10, retention_min_days: 30, batch_size: 50 },
  },
  patrol_retention: {
    run_days: 30,
    finding_days: 30,
    collector_evidence_days: 7,
    cleanup_batch_size: 100,
  },
  source_access: { url_allowlist: [], url_denylist: [] },
};

const EXECUTION_REVISION: ExecutionPolicyRevision = {
  id: HEAD.execution_revision_id,
  sequence: 1,
  schema_version: 1,
  policy: EXECUTION_POLICY,
  digest: `sha256:${"a".repeat(64)}`,
  created_by: "admin-1",
  note: "initial",
  restored_from_id: null,
  created_at: "2026-08-26T04:00:00Z",
};

const OPERATIONS_REVISION = {
  ...EXECUTION_REVISION,
  id: HEAD.operations_revision_id,
  policy: OPERATIONS_POLICY,
};

beforeEach(() => {
  mocks.getExecution.mockResolvedValue({ head: HEAD, revision: EXECUTION_REVISION });
  mocks.getOperations.mockResolvedValue({ head: HEAD, revision: OPERATIONS_REVISION });
  mocks.listExecutionRevisions.mockResolvedValue({
    items: [EXECUTION_REVISION],
    limit: 20,
    offset: 0,
  });
  mocks.listOperationsRevisions.mockResolvedValue({
    items: [OPERATIONS_REVISION],
    limit: 20,
    offset: 0,
  });
  mocks.createExecution.mockResolvedValue({ head: HEAD, revision: EXECUTION_REVISION });
  mocks.createOperations.mockResolvedValue({ head: HEAD, revision: OPERATIONS_REVISION });
  mocks.restoreExecution.mockResolvedValue({ head: HEAD, revision: EXECUTION_REVISION });
  mocks.restoreOperations.mockResolvedValue({ head: HEAD, revision: OPERATIONS_REVISION });
});

afterEach(() => {
  vi.clearAllMocks();
  document.body.replaceChildren();
});

describe("Runtime Policy settings", () => {
  it("preserves edits and offers reload on a head conflict", async () => {
    mocks.createExecution.mockRejectedValue(
      new ApiError(409, "conflict", { version: 9 }, "runtimePolicy.headConflict"),
    );
    const { container, unmount } = await renderComponent(
      <NextIntlClientProvider locale="en" messages={en}>
        <RuntimePolicySettings />
      </NextIntlClientProvider>,
    );
    await act(async () => Promise.resolve());

    const input = container.querySelector(
      'input[aria-label="Maximum iterations"]',
    ) as HTMLInputElement;
    expect(input).toBeTruthy();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(input, "20");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const note = container.querySelector('input[aria-label="Change note"]') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(note, "Tune agent budget");
      note.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const save = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "Save execution policy",
    );
    await act(async () => save?.click());

    expect(input.value).toBe("20");
    expect(container.textContent).toContain("Reload active revision");
    await unmount();
  });

  it("requires confirmation before restoring a revision", async () => {
    const { container, unmount } = await renderComponent(
      <NextIntlClientProvider locale="en" messages={en}>
        <PolicyHistory
          kind="execution"
          head={HEAD}
          revisions={[
            {
              ...EXECUTION_REVISION,
              id: "33333333-3333-4333-8333-333333333333",
              sequence: 1,
            },
          ]}
          onRestored={vi.fn()}
        />
      </NextIntlClientProvider>,
    );
    const restore = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Restore revision 1"),
    );
    await act(async () => restore?.click());
    expect(mocks.restoreExecution).not.toHaveBeenCalled();
    const confirm = Array.from(document.body.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Confirm restore"),
    );
    await act(async () => confirm?.click());
    expect(mocks.restoreExecution).toHaveBeenCalledOnce();
    await unmount();
  });
});
