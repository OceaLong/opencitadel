// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
import { ImportPreview } from "./import-preview";
const preview = {
  import_id: "i",
  dataset_id: "d",
  revision: 2,
  input_digest: "a".repeat(64),
  errors: [],
  added: ["new"],
  replaced: ["old"],
  removed: ["deleted"],
  expires_at: "2099-01-01T00:00:00Z",
};
test("shows replacement/removal and requires confirmation, matching revision and zero errors", async () => {
  const apply = vi.fn();
  const { container, root, unmount } = await renderComponent(
    <ImportPreview preview={preview} revision={2} pending={false} canManage onApply={apply} />,
  );
  expect(container.textContent).toContain("deleted");
  expect(container.querySelector("button")?.disabled).toBe(true);
  await act(async () => {
    (container.querySelector('input[type="checkbox"]') as HTMLInputElement).click();
  });
  expect(container.querySelector("button")?.disabled).toBe(false);
  await act(async () => {
    root.render(
      <ImportPreview
        preview={{
          ...preview,
          errors: [{ row: 7, field: "input", code: "empty", message: "Invalid input" }],
        }}
        revision={2}
        pending={false}
        canManage
        onApply={apply}
      />,
    );
  });
  expect(container.textContent).toContain("7");
  expect(container.querySelector("button")?.disabled).toBe(true);
  await act(async () => {
    root.render(
      <ImportPreview preview={preview} revision={3} pending={false} canManage onApply={apply} />,
    );
  });
  expect(container.querySelector("button")?.disabled).toBe(true);
  expect(apply).not.toHaveBeenCalled();
  await unmount();
});
