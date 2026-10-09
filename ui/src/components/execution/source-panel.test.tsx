// @vitest-environment jsdom
import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";
import { SourcePanel } from "./source-panel";

test("fixed canonical identities remain visible and fallback requires explicit selection", async () => {
  const change = vi.fn();
  const { container, unmount } = await renderComponent(
    <NextIntlClientProvider locale="en" messages={en}>
      <SourcePanel
        citation={{
          citation_id: "c",
          resource_kind: "knowledge_base",
          knowledge_base_id: "kb",
          version_id: "v1",
          document_revision_id: "r1",
          doc_id: "doc",
          chunk_id: "chunk",
          page_no: 2,
          availability: "available",
        }}
        locator="original"
        onLocatorChange={change}
        body={<p>Fixed body</p>}
      />
    </NextIntlClientProvider>,
  );
  expect(container.textContent).toContain("v1");
  expect(container.textContent).toContain("r1");
  expect(container.textContent).toContain("chunk");
  expect(change).not.toHaveBeenCalled();
  await act(async () => {
    (container.querySelector('[data-source-fallback="page"]') as HTMLButtonElement).click();
  });
  expect(change).toHaveBeenCalledWith("page");
  await unmount();
});
test("file citations keep separate immutable file identity and expose no KB fallback", async () => {
  const { container, unmount } = await renderComponent(
    <NextIntlClientProvider locale="en" messages={en}>
      <SourcePanel
        citation={{
          citation_id: "file-c",
          resource_kind: "file",
          file_id: "f1",
          availability: "available",
        }}
        body={<p>File body</p>}
      />
    </NextIntlClientProvider>,
  );
  expect(container.textContent).toContain("f1");
  expect(container.querySelector("[data-source-fallback]")).toBeNull();
  await unmount();
});
