// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({ upload: vi.fn(), list: vi.fn(), versions: vi.fn() }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/lib/api/file", () => ({ fileApi: { uploadFile: mocks.upload } }));
vi.mock("@/lib/api/knowledge", () => ({
  knowledgeApi: { list: mocks.list, listVersions: mocks.versions },
}));
import { ResourcePicker } from "./resource-picker";
const access = { workspaceId: "one", canManage: true, canRun: false, canRegister: false };
test.each(["success", "failure"])(
  "unmounted upload rejects old %s without modifying the next case",
  async (kind) => {
    mocks.list.mockResolvedValue({ knowledge_bases: [] });
    let resolve!: (v: { id: string }) => void, reject!: (e: Error) => void;
    mocks.upload.mockImplementation(
      () =>
        new Promise((yes, no) => {
          resolve = yes;
          reject = no;
        }),
    );
    const old = vi.fn(),
      current = vi.fn();
    const view = await renderComponent(
      <ResourcePicker key="case1" access={access} attachments={[]} bindings={[]} onChange={old} />,
    );
    const input = view.container.querySelector("input[type=file]")!;
    Object.defineProperty(input, "files", { value: [new File(["content"], "fixture.txt")] });
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const options = mocks.upload.mock.lastCall![1];
    expect(options.workspaceId).toBe("one");
    await act(async () =>
      view.root.render(
        <ResourcePicker
          key="case2"
          access={{ ...access, workspaceId: "two" }}
          attachments={[]}
          bindings={[]}
          onChange={current}
        />,
      ),
    );
    expect(options.signal.aborted).toBe(true);
    await act(async () => {
      if (kind === "success") resolve({ id: "oldfile" });
      else reject(new Error("old upload"));
    });
    expect(old).not.toHaveBeenCalled();
    expect(current).not.toHaveBeenCalled();
    expect(view.container.textContent).not.toContain("failed");
    await view.unmount();
  },
);
