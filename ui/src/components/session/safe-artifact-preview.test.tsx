// @vitest-environment jsdom
import { expect, test } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { SafeArtifactPreview } from "./safe-artifact-preview";

test("rich preview retains semantic text but no active URL or scripting capabilities", async () => {
  const { container, unmount } = await renderComponent(
    <SafeArtifactPreview
      notice="Static preview"
      title="Preview"
      content={
        '<h1>Hello</h1><a href="/escape">Link</a><img src="/leak"><style>@import "/leak";</style><script>parent.document.body.remove()</script><form action="/leak"><input></form>'
      }
    />,
  );
  const frame = container.querySelector("iframe")!;
  const parsed = document.createElement("template");
  parsed.innerHTML = frame.srcdoc;
  expect(parsed.content.querySelector("h1")?.textContent).toBe("Hello");
  expect(parsed.content.querySelector("[href],[src],script,style,form,input")).toBeNull();
  expect(frame.getAttribute("sandbox")).toBe("");
  await unmount();
});
