// @vitest-environment jsdom

import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";
import zh from "../../../messages/zh.json";

const mocks = vi.hoisted(() => ({ create: vi.fn() }));
vi.mock("@/lib/api/knowledge", () => ({ knowledgeApi: mocks }));

import { CreateKBDialog } from "./create-kb-dialog";

afterEach(() => {
  vi.clearAllMocks();
  document.body.replaceChildren();
});

describe.each([
  ["en", en],
  ["zh", zh],
] as const)("Create knowledge base %s", (locale, messages) => {
  it("submits the explicit immutable embedding revision policy", async () => {
    mocks.create.mockResolvedValue({ id: "strict-kb", name: "strict" });
    const { unmount } = await renderComponent(
      <NextIntlClientProvider locale={locale} messages={messages}>
        <CreateKBDialog open onOpenChange={vi.fn()} onCreated={vi.fn()} />
      </NextIntlClientProvider>,
    );
    const checkbox = document.querySelector("#kb-verified-embedding") as HTMLInputElement;
    await act(async () => checkbox.click());
    const create = Array.from(document.body.querySelectorAll("button")).find(
      (button) => button.textContent === messages.common.create,
    );
    await act(async () => create?.click());
    expect(mocks.create).toHaveBeenCalledWith({
      name: messages.knowledge.createDialog.defaultName,
      settings: { embedding_revision_policy: "verified_only" },
    });
    await unmount();
  });

  it("explains policy inheritance and leaves enhancement settings to the server", async () => {
    const created = {
      id: "kb-server-policy",
      name: "Policy inherited knowledge base",
      status: "pending",
      doc_count: 0,
      chunk_count: 0,
      settings: { vector_enabled: true, graphrag: { enabled: true } },
    };
    mocks.create.mockResolvedValue(created);
    const onCreated = vi.fn();
    const { unmount } = await renderComponent(
      <NextIntlClientProvider locale={locale} messages={messages}>
        <CreateKBDialog open onOpenChange={vi.fn()} onCreated={onCreated} />
      </NextIntlClientProvider>,
    );
    expect(document.body.textContent).toContain(messages.knowledge.createDialog.description);
    expect(document.body.textContent).toContain(messages.knowledge.createDialog.policyHint);
    const input = document.querySelector("#kb-name") as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(input, created.name);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const create = Array.from(document.body.querySelectorAll("button")).find(
      (button) => button.textContent === messages.common.create,
    );
    await act(async () => create?.click());
    expect(mocks.create).toHaveBeenCalledWith({ name: created.name });
    expect(onCreated).toHaveBeenCalledWith(created);
    await unmount();
  });
});
