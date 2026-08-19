// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { PageTitleProvider, usePageTitle, useReportPageTitle } from "./page-title-provider";

afterEach(() => {
  document.body.replaceChildren();
});

function Reporter({ title }: { title?: string }) {
  useReportPageTitle(title);
  return null;
}

function Display() {
  return <span data-testid="title">{usePageTitle() ?? "none"}</span>;
}

it("reports and clears the page title", async () => {
  const { container, root, unmount } = await renderComponent(
    <PageTitleProvider>
      <Reporter title="nightly-check" />
      <Display />
    </PageTitleProvider>,
  );
  expect(container.querySelector("[data-testid=title]")?.textContent).toBe("nightly-check");
  const { act } = await import("react");
  await act(async () => {
    root.render(
      <PageTitleProvider>
        <Reporter title={undefined} />
        <Display />
      </PageTitleProvider>,
    );
  });
  expect(container.querySelector("[data-testid=title]")?.textContent).toBe("none");
  await unmount();
});
