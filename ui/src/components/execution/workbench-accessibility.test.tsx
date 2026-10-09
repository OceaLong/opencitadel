// @vitest-environment jsdom
import { act, useState } from "react";
import { expect, test, vi } from "vitest";

import type { WorkbenchLayout } from "@/lib/execution-view/layout-preferences";
import { parseSelection } from "@/lib/execution-view/url-state";

import { renderComponent } from "@/test-utils/render";

vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
import { WorkbenchShell } from "./workbench-shell";

function Probe() {
  const [selection, setSelection] = useState(
    parseSelection("run=r&at=opaque&step=attempt").selection,
  );
  const [layout, setLayout] = useState<WorkbenchLayout>({ conversationOpen: false });
  return (
    <WorkbenchShell
      selection={selection}
      onSelectionChange={setSelection}
      layout={layout}
      onLayoutChange={setLayout}
      view={<button>Step</button>}
      chat={<input aria-label="Draft" defaultValue="retained" />}
      onReturnLive={() => setSelection({ ...selection, at: null })}
      notice={<output>{JSON.stringify(selection)}</output>}
    />
  );
}

test("scoped modifier shortcuts retain selection and ignore editable fields", async () => {
  const result = await renderComponent(<Probe />);
  const workbench = result.container.querySelector('[data-testid="workbench"]');
  expect(workbench).not.toBeNull();
  const press = async (target: Element, key: string) =>
    act(async () => {
      target.dispatchEvent(new KeyboardEvent("keydown", { key, altKey: true, bubbles: true }));
    });
  await press(workbench!, "2");
  expect(result.container.querySelector("output")?.textContent).toContain('"view":"debug"');
  expect(result.container.querySelector("output")?.textContent).toContain('"at":"opaque"');
  expect(result.container.querySelector("output")?.textContent).toContain('"stepId":"attempt"');
  await press(workbench!, "c");
  expect(result.container.querySelector<HTMLElement>("[data-conversation]")?.hidden).toBe(false);
  await press(result.container.querySelector("input")!, "1");
  expect(result.container.querySelector("output")?.textContent).toContain('"view":"debug"');
  await press(workbench!, "l");
  expect(result.container.querySelector("output")?.textContent).toContain('"at":null');
  await result.unmount();
});

test("shortcuts can be remapped and disabled in workbench preferences", async () => {
  const result = await renderComponent(<Probe />);
  const preferences = result.container.querySelector<HTMLButtonElement>(
    '[aria-label="keyboardPreferences"]',
  );
  expect(preferences).not.toBeNull();
  await act(async () => preferences!.click());
  const debug = result.container.querySelector<HTMLInputElement>('[aria-label="shortcutDebug"]');
  expect(debug).not.toBeNull();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(debug, "d");
    debug!.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const workbench = result.container.querySelector('[data-testid="workbench"]')!;
  await act(async () =>
    workbench.dispatchEvent(
      new KeyboardEvent("keydown", { key: "d", altKey: true, bubbles: true }),
    ),
  );
  expect(result.container.querySelector("output")?.textContent).toContain('"view":"debug"');
  await act(async () =>
    result.container.querySelector<HTMLInputElement>('[aria-label="shortcutsEnabled"]')!.click(),
  );
  await act(async () =>
    workbench.dispatchEvent(
      new KeyboardEvent("keydown", { key: "1", altKey: true, bubbles: true }),
    ),
  );
  expect(result.container.querySelector("output")?.textContent).toContain('"view":"debug"');
  await result.unmount();
});
