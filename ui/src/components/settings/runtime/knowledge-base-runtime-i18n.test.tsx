// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { NextIntlClientProvider } from "next-intl";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import en from "../../../../messages/en.json";
import zh from "../../../../messages/zh.json";

const reactActEnvironment = globalThis as typeof globalThis & {
  IS_REACT_ACT_ENVIRONMENT?: boolean;
};
const originalReactActEnvironment =
  reactActEnvironment.IS_REACT_ACT_ENVIRONMENT;

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
vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));
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

beforeAll(() => {
  reactActEnvironment.IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  document.body.replaceChildren();
});

afterAll(() => {
  if (originalReactActEnvironment === undefined) {
    delete reactActEnvironment.IS_REACT_ACT_ENVIRONMENT;
  } else {
    reactActEnvironment.IS_REACT_ACT_ENVIRONMENT =
      originalReactActEnvironment;
  }
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
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <NextIntlClientProvider locale={locale} messages={messages}>
          <KnowledgeBaseRuntimeForm isAdmin />
        </NextIntlClientProvider>,
      );
      await Promise.resolve();
      await Promise.resolve();
    });

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
    await act(async () => root.unmount());
  });
});
