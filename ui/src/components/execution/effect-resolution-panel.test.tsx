// @vitest-environment jsdom
import { act } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  type EffectOperation,
  type EffectRequest,
  executionEffectsApi,
} from "@/lib/api/execution-effects";
import { ApiError } from "@/lib/api/fetch";
import { fileApi } from "@/lib/api/file";

import { renderComponent } from "@/test-utils/render";

import { EffectResolutionPanel } from "./effect-resolution-panel";

vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
const operation = (update: Partial<EffectOperation> = {}): EffectOperation => ({
  operation_id: "op",
  run_id: "run",
  invocation_id: "inv",
  activity_id: "activity",
  revision: 3,
  state: "unknown",
  capability: { resolve: true },
  wait_reason: "external_outcome_unknown",
  contract_digest: "a".repeat(64),
  arguments_digest: "b".repeat(64),
  target_digest: "c".repeat(64),
  provider_identity: "actuator",
  allowed_actions: ["query", "resolve", "abandon"],
  verification_binding: { action: "scale_workload" },
  receipts: [],
  query_history: [],
  accounting_state: "independent",
  contract_drift: null,
  last_confirmed_fact: null,
  ...update,
});
const request: EffectRequest = {
  request_id: "request-1",
  run_id: "run",
  operation_id: "op",
  expected_revision: 3,
  status: "pending",
  error_code: null,
  created_at: "2026-10-13",
  updated_at: "2026-10-13",
};
const panel = (revision = 1, readOnly = false) => (
  <EffectResolutionPanel
    runId="run"
    workspaceId="team"
    actorId="owner"
    revision={revision}
    readOnly={readOnly}
  />
);
const button = (container: HTMLElement, name: string) =>
  [...container.querySelectorAll("button")].find((b) => b.textContent === name)!;
async function setValue(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
  await act(async () => {
    const proto =
      element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
beforeEach(() => {
  vi.spyOn(executionEffectsApi, "list").mockResolvedValue([operation()]);
  vi.spyOn(executionEffectsApi, "get").mockResolvedValue(operation());
  vi.spyOn(executionEffectsApi, "query").mockResolvedValue(request);
  vi.spyOn(executionEffectsApi, "resolve").mockResolvedValue(request);
  vi.spyOn(executionEffectsApi, "abandon").mockResolvedValue(request);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  document.body.replaceChildren();
});

test("conflicting receipts explain quarantine and expose no recovery action", async () => {
  const quarantined = operation({
    state: "applied",
    wait_reason: "external_evidence_conflict",
    allowed_actions: [],
  });
  vi.mocked(executionEffectsApi.list).mockResolvedValue([quarantined]);
  vi.mocked(executionEffectsApi.get).mockResolvedValue(quarantined);
  const r = await renderComponent(panel());
  expect(r.container.textContent).toContain("evidenceConflict");
  expect(button(r.container, "query")).toBeUndefined();
  expect(button(r.container, "resolve")).toBeUndefined();
  expect(button(r.container, "supplyResult")).toBeUndefined();
});

test("query keeps original operation and displays durable request separately from billing", async () => {
  const r = await renderComponent(panel());
  expect(r.container.textContent).toContain("independentAccounting");
  expect(r.container.textContent).not.toContain("rerun");
  await act(async () => button(r.container, "query").click());
  expect(executionEffectsApi.query).toHaveBeenCalledWith(
    "run",
    "op",
    3,
    expect.any(String),
    expect.objectContaining({ workspaceId: "team" }),
  );
  expect(r.container.textContent).toContain("request-1");
  expect(r.container.textContent).toContain("pending");
  await r.unmount();
});

test("uncertain request retry reuses its idempotency key", async () => {
  vi.mocked(executionEffectsApi.query).mockRejectedValueOnce(new Error("response lost"));
  const r = await renderComponent(panel());
  await act(async () => button(r.container, "query").click());
  await act(async () => button(r.container, "query").click());
  const calls = vi.mocked(executionEffectsApi.query).mock.calls;
  expect(calls[0][3]).toBe(calls[1][3]);
  await r.unmount();
});

test("reloaded server history and parent event revision restore current request state", async () => {
  vi.mocked(executionEffectsApi.get).mockResolvedValue(operation({ query_history: [request] }));
  const r = await renderComponent(panel());
  expect(r.container.textContent).toContain("request-1");
  vi.mocked(executionEffectsApi.get).mockResolvedValue(
    operation({
      state: "applied",
      query_history: [{ ...request, status: "completed" }],
      allowed_actions: [],
    }),
  );
  await act(async () => r.root.render(panel(2)));
  expect(r.container.textContent).toContain("completed");
  expect(r.container.textContent).toContain("states.applied");
  expect(button(r.container, "query")).toBeUndefined();
  expect(r.container.textContent).toContain("independentAccounting");
  await r.unmount();
});

test("auditor reads evidence and never sees write controls even with stale allowed actions", async () => {
  const r = await renderComponent(panel(1, true));
  expect(r.container.textContent).toContain("op");
  expect(r.container.querySelector("button")).toBeNull();
  await r.unmount();
});

test("revoked access removes private operation and all commands", async () => {
  vi.mocked(executionEffectsApi.query).mockRejectedValue(new ApiError(403, "denied"));
  const r = await renderComponent(panel());
  await act(async () => button(r.container, "query").click());
  expect(r.container.textContent).toBe("accessRevoked");
  await r.unmount();
});

test("contract drift and capabilities stay visible without allowing old approval reuse", async () => {
  vi.mocked(executionEffectsApi.get).mockResolvedValue(
    operation({ contract_drift: { target: "changed" }, allowed_actions: [] }),
  );
  const r = await renderComponent(panel());
  expect(r.container.textContent).toContain("contractDrift");
  expect(r.container.textContent).toContain("changed");
  expect(r.container.querySelector("button")).toBeNull();
  await r.unmount();
});

test("abandon requires an explicit reason and never sends billing fields", async () => {
  const r = await renderComponent(panel());
  await act(async () => button(r.container, "abandon").click());
  expect(button(r.container, "submit").disabled).toBe(true);
  expect(r.container.textContent).toContain("abandonWarning");
  await setValue(r.container.querySelector("textarea")!, "Stop business waiting");
  await act(async () => button(r.container, "submit").click());
  expect(executionEffectsApi.abandon).toHaveBeenCalledWith(
    "run",
    "op",
    3,
    "Stop business waiting",
    expect.any(String),
    expect.anything(),
  );
  await r.unmount();
});

test("uploaded evidence package freezes original identity and human attestation", async () => {
  let uploaded!: File;
  vi.spyOn(fileApi, "uploadFile").mockImplementation(async ({ file }) => {
    uploaded = file;
    return {
      id: "file-1",
      filename: file.name,
      filepath: "",
      key: "",
      extension: "json",
      mime_type: "application/json",
      size: file.size,
      content_digest: "d".repeat(64),
    };
  });
  const r = await renderComponent(panel());
  await act(async () => button(r.container, "resolve").click());
  expect(button(r.container, "submit").disabled).toBe(true);
  const file = new File(["Audit record: op applied"], "audit.txt", { type: "text/plain" });
  Object.defineProperty(file, "text", { value: async () => "Audit record: op applied" });
  const input = r.container.querySelector('input[type="file"]')!;
  Object.defineProperty(input, "files", { value: [file] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  const packageText = await new Promise<string>((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.readAsText(uploaded);
  });
  expect(JSON.parse(packageText)).toMatchObject({
    schema_version: 1,
    operation_id: "op",
    contract_digest: "a".repeat(64),
    arguments_digest: "b".repeat(64),
    target_digest: "c".repeat(64),
    evidence: { filename: "audit.txt", content: "Audit record: op applied" },
  });
  await setValue(r.container.querySelector("input:not([type])")!, "Audit #1");
  await setValue(r.container.querySelector('input[type="datetime-local"]')!, "2026-10-13T10:00");
  const areas = r.container.querySelectorAll("textarea");
  await setValue(areas[0], "Checked original operation ID");
  await setValue(areas[1], "Verified from audit log");
  await act(async () => button(r.container, "submit").click());
  expect(executionEffectsApi.resolve).toHaveBeenCalledWith(
    "run",
    "op",
    expect.objectContaining({
      expected_revision: 3,
      decision: "applied",
      evidence_refs: [{ ref: "file://file-1", sha256: "d".repeat(64) }],
      attestation: expect.objectContaining({
        action: "scale_workload",
        target: "c".repeat(64),
        responsible_actor: "owner",
      }),
    }),
    expect.any(String),
    expect.objectContaining({ workspaceId: "team" }),
  );
  await r.unmount();
});
