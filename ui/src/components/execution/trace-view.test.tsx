// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import type { StepView } from "@/lib/api/types/execution-view";

import { renderComponent } from "@/test-utils/render";
const scroll = vi.hoisted(() => vi.fn());
const windowControl = vi.hoisted(() => ({ shift: (() => {}) as (start: number) => void }));
vi.mock("next-intl", () => ({
  useTranslations: () => (k: string, values?: Record<string, unknown>) =>
    k === "timing" ? `timing offset=${values?.offset}` : k,
}));
vi.mock("@tanstack/react-virtual", async () => {
  const { useState } = await import("react");
  return {
    useVirtualizer: ({
      count,
      getItemKey,
      estimateSize,
    }: {
      count: number;
      getItemKey: (i: number) => string;
      estimateSize: (i: number) => number;
    }) => {
      const [start, setStart] = useState(0);
      windowControl.shift = setStart;
      const bounded = Math.min(start, Math.max(0, count - 30));
      return {
        getTotalSize: () => count * 36,
        getVirtualItems: () =>
          Array.from({ length: Math.min(count, 30) }, (_, offset) => {
            const i = bounded + offset;
            return { index: i, key: getItemKey(i), start: i * 36, size: estimateSize(i) };
          }),
        scrollToIndex: (index: number, options: unknown) => {
          scroll(index, options);
          if (index < bounded || index >= bounded + 30) setStart(Math.max(0, index - 15));
        },
      };
    },
  };
});
import { buildTraceRows } from "@/lib/execution-view/trace-layout";

import { TraceRow } from "./trace-row";
import { TraceView } from "./trace-view";
const steps = (n: number) =>
  Array.from(
    { length: n },
    (_, i) =>
      ({
        step_id: `s${i}`,
        run_id: "r",
        kind: "tool",
        tool_name: "same",
        status: "running",
        started_at: "2026-01-01T00:00:00Z",
        attempt_id: `a${i}`,
        projection_revision: 1,
        completeness: { state: "complete" },
      }) as StepView,
  );
test("10k rows share one bounded virtual tree/timeline with exact selection and keyboard focus", async () => {
  const select = vi.fn();
  const result = await renderComponent(
    <TraceView
      steps={steps(10000)}
      selection={null}
      onSelectStep={select}
      asOf="2026-01-01T00:00:05Z"
    />,
  );
  expect(result.container.querySelectorAll("[data-trace-row]").length).toBe(30);
  const first = result.container.querySelector<HTMLButtonElement>('[data-step="s0"]')!;
  await act(async () =>
    first.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })),
  );
  expect(document.activeElement?.getAttribute("data-step")).toBe("s1");
  await act(async () => (document.activeElement as HTMLElement).click());
  expect(select).toHaveBeenCalledWith("s1");
  for (const row of result.container.querySelectorAll("[data-trace-row]"))
    expect(row.querySelector("[data-trace-span]")).not.toBeNull();
  expect(scroll).toHaveBeenCalledWith(1, { align: "auto" });
  await result.unmount();
});
test("logical disclosure reveals independently selectable attempts and unknown timing text", async () => {
  const data = steps(2).map((s) => ({ ...s, logical_step_id: "logical", started_at: null }));
  const select = vi.fn();
  const r = await renderComponent(
    <TraceView steps={data} selection={null} onSelectStep={select} asOf={null} />,
  );
  expect(r.container.querySelectorAll("[data-trace-row]")).toHaveLength(1);
  await act(async () => r.container.querySelector<HTMLButtonElement>("[data-disclosure]")!.click());
  expect(r.container.querySelectorAll("[data-trace-row]")).toHaveLength(3);
  expect(r.container.textContent).toContain("unknownTiming");
  await act(async () => r.container.querySelector<HTMLButtonElement>('[data-step="s1"]')!.click());
  expect(select).toHaveBeenCalledWith("s1");
  await r.unmount();
});
test("deep selection reveals its exact virtual index; narrow mode retains timing", async () => {
  scroll.mockClear();
  const r = await renderComponent(
    <TraceView
      steps={steps(10000)}
      selection="s9999"
      onSelectStep={() => {}}
      asOf="2026-01-01T00:00:05Z"
      viewport={{ narrow: true }}
    />,
  );
  expect(scroll).toHaveBeenCalledWith(9999, { align: "auto" });
  expect(r.container.querySelector("[data-trace-span]")).toBeNull();
  expect(r.container.textContent).toContain("timing");
  await r.unmount();
});
test("keyboard can collapse auto-revealed selected ancestry and reopen it", async () => {
  const data = steps(2).map((s) => ({ ...s, logical_step_id: "logical" }));
  const r = await renderComponent(
    <TraceView steps={data} selection="s1" onSelectStep={() => {}} asOf={null} />,
  );
  expect(r.container.querySelectorAll("[data-trace-row]")).toHaveLength(3);
  await act(async () =>
    r.container
      .querySelector<HTMLButtonElement>('[data-row-key="logical:logical"]')!
      .dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true })),
  );
  expect(r.container.querySelectorAll("[data-trace-row]")).toHaveLength(1);
  await act(async () =>
    r.container
      .querySelector<HTMLButtonElement>('[data-row-key="logical:logical"]')!
      .dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })),
  );
  expect(r.container.querySelectorAll("[data-trace-row]")).toHaveLength(3);
  await r.unmount();
});

test("focus crosses the virtual window and lands on the newly mounted exact attempt", async () => {
  const r = await renderComponent(
    <TraceView steps={steps(10000)} selection={null} onSelectStep={() => {}} asOf={null} />,
  );
  await act(async () =>
    r.container
      .querySelector<HTMLButtonElement>('[data-step="s29"]')!
      .dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })),
  );
  expect(document.activeElement?.getAttribute("data-step")).toBe("s30");
  expect(r.container.querySelectorAll("[data-trace-row]")).toHaveLength(30);
  await r.unmount();
});

test("panning the axis never changes the actual start offset label", async () => {
  const row = buildTraceRows(steps(1), new Set(), {})[0];
  const r = await renderComponent(
    <TraceRow
      row={row}
      expanded={false}
      selected={false}
      tabIndex={0}
      narrow={false}
      interval={{ start: 1000, end: 3000, open: false }}
      origin={2000}
      baseline={0}
      range={1000}
      complete
      work={null}
      onToggle={() => {}}
      onSelect={() => {}}
      onKeyDown={() => {}}
      buttonRef={() => {}}
    />,
  );
  expect(r.container.textContent).toContain("timing offset=1.000");
  await r.unmount();
});
test("tree width is adjustable within the native trace layout", async () => {
  const r = await renderComponent(
    <TraceView steps={steps(1)} selection={null} onSelectStep={() => {}} asOf={null} />,
  );
  const width = r.container.querySelector<HTMLInputElement>('input[aria-label="treeWidth"]');
  expect(width).not.toBeNull();
  expect([width?.min, width?.max, width?.value]).toEqual(["220", "360", "280"]);
  await r.unmount();
});

function stagedTrace() {
  const roots = steps(200);
  const selected = { ...steps(1)[0], step_id: "selected", parent_step_id: "ancestor" };
  const ancestor = { ...steps(1)[0], step_id: "ancestor", parent_step_id: "s0" };
  return { initial: [...roots, selected], complete: [...roots, selected, ancestor] };
}
test("same-cut ancestor arrival keeps the selected attempt visible at its new index", async () => {
  const data = stagedTrace();
  scroll.mockClear();
  const r = await renderComponent(
    <TraceView steps={data.initial} selection="selected" onSelectStep={() => {}} asOf={null} />,
  );
  expect(scroll).toHaveBeenLastCalledWith(200, { align: "auto" });
  expect(r.container.querySelector('[data-step="selected"]')).not.toBeNull();
  await act(async () =>
    r.root.render(
      <TraceView steps={data.complete} selection="selected" onSelectStep={() => {}} asOf={null} />,
    ),
  );
  expect(scroll).toHaveBeenLastCalledWith(2, { align: "auto" });
  expect(r.container.querySelector('[data-step="selected"]')).not.toBeNull();
  await r.unmount();
});
test("ancestor arrival respects deliberate wheel scrolling away from the selection", async () => {
  const data = stagedTrace();
  const r = await renderComponent(
    <TraceView steps={data.initial} selection="selected" onSelectStep={() => {}} asOf={null} />,
  );
  await act(async () => {
    r.container
      .querySelector('[role="tree"]')!
      .dispatchEvent(new WheelEvent("wheel", { bubbles: true, deltaY: 100 }));
    windowControl.shift(80);
  });
  scroll.mockClear();
  await act(async () =>
    r.root.render(
      <TraceView steps={data.complete} selection="selected" onSelectStep={() => {}} asOf={null} />,
    ),
  );
  expect(scroll).not.toHaveBeenCalled();
  expect(r.container.querySelector('[data-step="selected"]')).toBeNull();
  await r.unmount();
});
test.each(["none", "selected", "focused"])(
  "manual virtual-window shift retains a mounted keyboard entry (%s)",
  async (mode) => {
    const r = await renderComponent(
      <TraceView
        steps={steps(10000)}
        selection={mode === "selected" ? "s0" : null}
        onSelectStep={() => {}}
        asOf={null}
      />,
    );
    if (mode === "focused")
      await act(async () =>
        r.container.querySelector<HTMLButtonElement>('[data-step="s10"]')!.focus(),
      );
    // Shift only the virtualizer's observed viewport, never TraceView's focus/scroll helper.
    await act(async () => windowControl.shift(500));
    const entry = r.container.querySelector<HTMLButtonElement>('[data-row-key][tabindex="0"]');
    expect(entry).not.toBeNull();
    expect(entry!.getAttribute("data-step")).toBe("s500");
    if (mode === "focused") {
      await act(async () => windowControl.shift(0));
      expect(
        r.container.querySelector('[data-row-key][tabindex="0"]')?.getAttribute("data-step"),
      ).toBe("s10");
      await act(async () => windowControl.shift(500));
    }
    const reentry = r.container.querySelector<HTMLButtonElement>('[data-row-key][tabindex="0"]')!;
    await act(async () => {
      reentry.focus();
      reentry.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    });
    expect(document.activeElement?.getAttribute("data-step")).toBe("s501");
    await r.unmount();
  },
);
