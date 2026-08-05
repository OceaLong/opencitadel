// @vitest-environment jsdom

import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockSonner } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

import en from "../../../../messages/en.json";
import zh from "../../../../messages/zh.json";

const mocks = vi.hoisted(() => ({
  getSection: vi.fn().mockResolvedValue({
    graphrag: {
      max_chunks: 100,
      max_llm_calls: 50,
      max_tokens: 10000,
      deadline_seconds: 30,
    },
  }),
}));

vi.mock("@/lib/api/config", () => ({
  configApi: {
    getSection: mocks.getSection,
    updateSection: vi.fn(),
    deleteUserOverride: vi.fn(),
  },
}));
vi.mock("sonner", () => mockSonner());
vi.mock("./config-field", () => ({
  ConfigField: ({
    label,
    description,
  }: {
    label: string;
    description?: string;
  }) => <div>{`${label}|${description ?? ""}`}</div>,
}));

import { KnowledgeBaseRuntimeForm } from "./knowledge-base-runtime-form";

afterEach(() => {
  document.body.replaceChildren();
});

describe.each([
  ["en", en],
  ["zh", zh],
] as const)("knowledge-base i18n %s", (locale, messages) => {
  it("keeps library keys and renders localized GraphRAG budgets", async () => {
    const knowledge = messages.knowledge as unknown as Record<
      string,
      string
    >;
    for (const key of [
      "partialFailureWarning",
      "readyDocCount",
      "showDocuments",
      "loadMoreDocuments",
    ]) {
      expect(knowledge[key]).toBeTruthy();
    }

    const runtime = messages.settingsRuntime;
    const labels = runtime.fields.knowledge_base as Record<string, string>;
    const descriptions = runtime.descriptions
      .knowledge_base as Record<string, string>;
    const { container, unmount } = await renderComponent(
      <NextIntlClientProvider locale={locale} messages={messages}>
        <KnowledgeBaseRuntimeForm isAdmin />
      </NextIntlClientProvider>,
    );

    for (const key of [
      "max_chunks",
      "max_llm_calls",
      "max_tokens",
      "deadline_seconds",
    ]) {
      expect(labels[key]).toBeTruthy();
      expect(descriptions[key]).toBeTruthy();
      expect(container.textContent).toContain(
        `${labels[key]}|${descriptions[key]}`,
      );
      expect(container.textContent).not.toContain(`${key}|`);
    }
    await unmount();
  });
});
