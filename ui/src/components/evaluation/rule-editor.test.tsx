// @vitest-environment jsdom
import { act, useState } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
import { RuleEditor } from "./rule-editor";
test("switching rule kinds emits complete defaults matching visible controls", async () => {
  const change = vi.fn();
  const { container, unmount } = await renderComponent(
    <RuleEditor rules={[{ kind: "text_exact", id: "r", expected: "ok" }]} onChange={change} />,
  );
  const kind = container.querySelector("select")!;
  await act(async () => {
    kind.value = "artifact";
    kind.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(change).toHaveBeenLastCalledWith([
    { kind: "artifact", id: "r", artifact_kind: "doc", schema: {} },
  ]);
  await act(async () => {
    kind.value = "jsonpath";
    kind.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(change).toHaveBeenLastCalledWith([
    { kind: "jsonpath", id: "r", path: "$.value", op: "eq", expected: "" },
  ]);
  await unmount();
});

test("removing the first same-kind rule preserves the remaining rule's JSON", async () => {
  function Editor() {
    const [rules, setRules] = useState([
      { id: "a", kind: "json_schema", schema: { const: "A" } },
      { id: "b", kind: "json_schema", schema: { const: "B" } },
    ]);
    return <RuleEditor rules={rules} onChange={(v) => setRules(v as typeof rules)} />;
  }
  const view = await renderComponent(<Editor />);
  await act(async () =>
    Array.from(view.container.querySelectorAll("button"))
      .find((b) => b.textContent === "remove")!
      .click(),
  );
  expect(JSON.parse(view.container.querySelector("textarea")!.value)).toEqual({ const: "B" });
  await view.unmount();
});
test("external JSON changes synchronize while unrelated renders preserve invalid local text", async () => {
  const change = vi.fn();
  const rules = [{ id: "a", kind: "json_schema", schema: { const: "A" } }];
  const view = await renderComponent(<RuleEditor rules={rules} onChange={change} />);
  const textarea = view.container.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(
      textarea,
      "{invalid",
    );
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () =>
    view.root.render(<RuleEditor rules={[{ ...rules[0], required: true }]} onChange={change} />),
  );
  expect(textarea.value).toBe("{invalid");
  expect(textarea.validity.valid).toBe(false);
  await act(async () =>
    view.root.render(
      <RuleEditor rules={[{ ...rules[0], schema: { const: "B" } }]} onChange={change} />,
    ),
  );
  expect(JSON.parse(textarea.value)).toEqual({ const: "B" });
  expect(textarea.validity.valid).toBe(true);
  await view.unmount();
});

test("removing A retains B's invalid draft and native submit block through editable rule IDs", async () => {
  const submit = vi.fn();
  function Editor() {
    const [rules, setRules] = useState([
      { id: "a", kind: "json_schema", schema: { const: "A" } },
      { id: "b", kind: "json_schema", schema: { const: "B" } },
    ]);
    return (
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit(rules);
        }}
      >
        <RuleEditor rules={rules} onChange={(v) => setRules(v as typeof rules)} />
        <button type="submit">save</button>
      </form>
    );
  }
  const view = await renderComponent(<Editor />);
  const input = async (node: HTMLInputElement | HTMLTextAreaElement, value: string) => {
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        node instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype,
        "value",
      )!.set!.call(node, value);
      node.dispatchEvent(new Event("input", { bubbles: true }));
    });
  };
  const b = view.container.querySelectorAll("textarea")[1];
  await input(b, "{unfinished B");
  await act(async () =>
    Array.from(view.container.querySelectorAll("button"))
      .find((v) => v.textContent === "remove")!
      .click(),
  );
  expect(view.container.querySelector("textarea")).toBe(b);
  expect(b.value).toBe("{unfinished B");
  expect(view.container.querySelector("form")!.checkValidity()).toBe(false);
  const id = Array.from(view.container.querySelectorAll("label")).find(
    (l) => l.textContent === "ruleId",
  )!;
  await input(document.getElementById(id.htmlFor) as HTMLInputElement, "renamed-b");
  expect(view.container.querySelector("textarea")).toBe(b);
  expect(b.value).toBe("{unfinished B");
  const save = view.container.querySelector<HTMLButtonElement>('button[type="submit"]')!;
  await act(async () => save.click());
  expect(submit).not.toHaveBeenCalled();
  await input(b, '{"const":"corrected B"}');
  expect(view.container.querySelector("form")!.checkValidity()).toBe(true);
  await act(async () => save.click());
  expect(submit).toHaveBeenLastCalledWith([
    { id: "renamed-b", kind: "json_schema", schema: { const: "corrected B" } },
  ]);
  await view.unmount();
});
