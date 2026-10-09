// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
import { type MatrixRow, ResultMatrix } from "./result-matrix";
const config = [{ id: "config", label: "Config" }];
test("empty matrix has an explicit missing state and still permits continuation", async () => {
  const more = vi.fn();
  const view = await renderComponent(
    <ResultMatrix
      rows={[]}
      configs={config}
      selection={null}
      onSelectResult={vi.fn()}
      onLoadMore={more}
    />,
  );
  expect(view.container.textContent).toContain("matrixEmpty");
  await act(async () => {
    Array.from(view.container.querySelectorAll("button"))
      .find((button) => button.textContent === "loadMore")!
      .click();
  });
  expect(more).toHaveBeenCalledOnce();
  await view.unmount();
});
test("1000 rows stay virtual, zero differs from missing, exact attempt and revision survive selection", async () => {
  const select = vi.fn();
  const rows: MatrixRow[] = Array.from({ length: 1000 }, (_, index) => ({
    id: `case-${index}`,
    label: "Very long case name ".repeat(20),
    results: [
      {
        id: `result-${index}`,
        configId: "config",
        repetition: 2,
        attempt: 1,
        runId: `run-${index}`,
        evaluationRevision: 7,
        resultRevision: 3,
        executionStatus: "succeeded",
        scoringStatus: "complete",
        value: index === 0 ? 0 : null,
      },
    ],
  }));
  const view = await renderComponent(
    <ResultMatrix rows={rows} configs={config} selection={null} onSelectResult={select} />,
  );
  expect(view.container.querySelectorAll("tbody tr").length).toBeLessThan(30);
  expect(view.container.textContent).toContain("missingScore");
  expect(view.container.querySelector("tbody td button")!.textContent).toContain("0");
  await act(async () => {
    (view.container.querySelector("tbody td button") as HTMLButtonElement).click();
  });
  expect(select).toHaveBeenCalledWith(rows[0].results[0]);
  expect(view.container.querySelector("tbody th")!.getAttribute("title")).toBe(rows[0].label);
  await view.unmount();
});
test("awaiting scoring, not run, failure and missing observation have separate labels", async () => {
  const base = {
    id: "r",
    configId: "config",
    repetition: 0,
    attempt: 0,
    runId: "run",
    evaluationRevision: 7,
    resultRevision: 3,
    executionStatus: "succeeded",
    scoringStatus: "pending",
    value: null,
  };
  const view = await renderComponent(
    <ResultMatrix
      rows={[
        base,
        { ...base, id: "queued", executionStatus: "queued" },
        { ...base, id: "failed", executionStatus: "failed" },
      ].map((result) => ({ id: result.id, label: result.id, results: [result] }))}
      configs={config}
      selection={null}
      onSelectResult={vi.fn()}
    />,
  );
  expect(view.container.textContent).toContain("matrixPendingScore");
  expect(view.container.textContent).toContain("matrixNotRun");
  expect(view.container.textContent).toContain("matrixFailed");
  await view.unmount();
});

test("mobile configuration tabs support arrow navigation and full case labels can be opened", async () => {
  const view = await renderComponent(
    <ResultMatrix
      rows={[{ id: "case", label: "A very long full case label", results: [] }]}
      configs={[
        { id: "a", label: "A" },
        { id: "b", label: "B" },
      ]}
      selection={null}
      onSelectResult={vi.fn()}
    />,
  );
  const first = view.container.querySelector('[role="tab"]') as HTMLButtonElement;
  await act(async () => {
    first.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  });
  expect(view.container.querySelector('[role="tab"][aria-selected="true"]')!.textContent).toBe("B");
  expect(document.activeElement?.textContent).toBe("B");
  await act(async () => {
    view.container.querySelector<HTMLButtonElement>("tbody th button")!.click();
  });
  expect(
    Array.from(view.container.querySelectorAll("button")).some(
      (button) => button.textContent === "close",
    ),
  ).toBe(true);
  await view.unmount();
});

test("repeat selection exposes exact attempt without growing every virtual cell", async () => {
  const results = Array.from({ length: 5 }, (_, i) => ({
    id: `r${i}`,
    configId: "config",
    repetition: i,
    attempt: i,
    runId: `run${i}`,
    evaluationRevision: 9,
    resultRevision: i,
    executionStatus: "succeeded",
    scoringStatus: "complete",
    value: i,
  }));
  const choose = vi.fn();
  const view = await renderComponent(
    <ResultMatrix
      rows={[{ id: "c", label: "Case", results }]}
      configs={config}
      selection={null}
      onSelectResult={choose}
    />,
  );
  expect(view.container.querySelectorAll("tbody td button")).toHaveLength(1);
  const select = view.container.querySelector("select")!;
  expect(select.options).toHaveLength(5);
  await act(async () => {
    select.value = "r4";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () =>
    view.container.querySelector<HTMLButtonElement>("tbody td button")!.click(),
  );
  expect(choose).toHaveBeenCalledWith(results[4]);
  await view.unmount();
});

test("budget exhaustion remains an explicit denial reason instead of generic execution failure", async () => {
  const view = await renderComponent(
    <ResultMatrix
      rows={[
        {
          id: "case",
          label: "Case",
          results: [
            {
              id: "r",
              configId: "config",
              repetition: 0,
              attempt: 0,
              runId: null,
              evaluationRevision: 1,
              resultRevision: 1,
              executionStatus: "blocked_budget",
              scoringStatus: "skipped",
              value: null,
            },
          ],
        },
      ]}
      configs={config}
      selection={null}
      onSelectResult={vi.fn()}
    />,
  );
  const button = view.container.querySelector("tbody td button")!;
  expect(button.textContent).toContain("stateBlockedBudget");
  expect(button.textContent).not.toContain("matrixFailed");
  expect(button.textContent).not.toContain("matrixScored");
  await view.unmount();
});

test("matrix receipts name accepted snapshot and actual result attempt", async () => {
  const rows: MatrixRow[] = [
    {
      id: "case",
      label: "Case",
      results: [
        {
          id: "result",
          configId: "config",
          repetition: 2,
          attempt: 1,
          runId: "run",
          evaluationRevision: 7,
          resultRevision: 3,
          executionStatus: "succeeded",
          scoringStatus: "complete",
          value: 0,
        },
      ],
    },
  ];
  const props = { rows, configs: config, selection: null, onSelectResult: vi.fn() };
  const view = await renderComponent(
    <ResultMatrix
      {...props}
      renderIdentity={{
        scopeId: "team",
        batchId: "batch",
        snapshotId: "issued",
        revision: 7,
        ready: true,
      }}
    />,
  );
  expect(
    view.container.querySelector("[data-public-snapshot]")?.getAttribute("data-public-snapshot"),
  ).toBe("issued");
  const cell = view.container.querySelector('[data-native-content="matrix-result"]');
  expect(cell?.getAttribute("data-public-result")).toBe("result");
  expect(cell?.getAttribute("data-public-result-revision")).toBe("3");
  expect(cell?.getAttribute("data-public-attempt")).toBe("1");
  expect(cell?.textContent).toContain("0");
  await act(async () =>
    view.root.render(
      <ResultMatrix
        {...props}
        renderIdentity={{
          scopeId: "team",
          batchId: "batch",
          snapshotId: "issued",
          revision: 7,
          ready: false,
        }}
      />,
    ),
  );
  expect(view.container.querySelector('[data-native-ready="true"]')).toBeNull();
  await view.unmount();
});
