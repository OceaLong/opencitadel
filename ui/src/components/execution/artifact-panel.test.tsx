// @vitest-environment jsdom
import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";
const mocks = vi.hoisted(() => ({ provenance: vi.fn(), get: vi.fn() }));
vi.mock("@/lib/api/execution-view", () => ({
  executionViewApi: { getProvenance: mocks.provenance },
}));
vi.mock("@/lib/api/artifacts", () => ({ artifactsApi: { get: mocks.get } }));
import { ArtifactPanel } from "./artifact-panel";
test("plural associations preserve run and step pairs and exact artifact citations", async () => {
  mocks.get.mockResolvedValue({ title: "Report", kind: "doc", status: "final" });
  mocks.provenance.mockResolvedValue([
    {
      producer_run_id: "run-A",
      producer_step_ids: ["s1", "s2"],
      binding_status: "bound",
      availability: "available",
      evidence_kind: "direct",
      citation_refs: [{ citation_id: "original", availability: "available" }],
      production_order: 4,
    },
    {
      producer_run_id: "run-B",
      producer_step_ids: ["s3", "s4"],
      binding_status: "bound",
      availability: "available",
      evidence_kind: "derived",
      citation_refs: [],
      production_order: 2,
    },
  ]);
  const select = vi.fn();
  const citation = vi.fn();
  const { container, unmount } = await renderComponent(
    <NextIntlClientProvider locale="en" messages={en}>
      <ArtifactPanel
        runId="run-A"
        at="cut"
        owner={{ key: "owner", workspaceId: "w", at: "cut" }}
        artifacts={[{ artifact_id: "a", version: 1, availability: "available" }]}
        onSelectProducer={select}
        onSelectCitation={citation}
        onRevoked={() => {}}
      />
    </NextIntlClientProvider>,
  );
  expect(container.querySelectorAll("[data-producer]")).toHaveLength(4);
  await act(async () => {
    (container.querySelector('[data-producer="run-B:s4"]') as HTMLButtonElement).click();
  });
  expect(select).toHaveBeenCalledWith({ runId: "run-B", stepId: "s4" });
  await act(async () => {
    (container.querySelector('[data-artifact-citation="original"]') as HTMLButtonElement).click();
  });
  expect(citation).toHaveBeenCalledWith(expect.objectContaining({ citation_id: "original" }));
  expect(mocks.provenance).toHaveBeenCalledWith(
    "a",
    1,
    expect.objectContaining({ workspaceId: "w" }),
    { run_id: "run-A", at: "cut" },
  );
  await unmount();
});

test("a late provenance response cannot repopulate a replaced owner", async () => {
  let old!: (value: unknown) => void;
  mocks.provenance
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          old = resolve;
        }),
    )
    .mockResolvedValue([]);
  mocks.get.mockResolvedValue({ title: "New authority report", kind: "doc", status: "final" });
  const show = (key: string) => (
    <NextIntlClientProvider locale="en" messages={en}>
      <ArtifactPanel
        runId="run"
        at="cut"
        owner={{ key, workspaceId: key, at: "cut" }}
        artifacts={[{ artifact_id: "a", version: 1, availability: "available" }]}
        onSelectProducer={() => {}}
        onSelectCitation={() => {}}
        onRevoked={() => {}}
      />
    </NextIntlClientProvider>
  );
  const { container, root, unmount } = await renderComponent(show("old"));
  await act(async () => {
    root.render(show("new"));
  });
  await act(async () =>
    old([
      {
        producer_run_id: "PRIVATE-OLD-RUN",
        producer_step_ids: ["private-old"],
        binding_status: "bound",
        availability: "available",
        evidence_kind: "direct",
        citation_refs: [],
      },
    ]),
  );
  expect(container.textContent).toContain("New authority report");
  expect(container.textContent).not.toContain("PRIVATE-OLD-RUN");
  await unmount();
});
