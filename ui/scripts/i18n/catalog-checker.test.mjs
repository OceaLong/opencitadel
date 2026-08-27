import assert from "node:assert/strict";
import test from "node:test";

import { analyzeCatalog, assertCatalogClean } from "./catalog-checker.mjs";

function analyze({
  en = { common: { save: "Save" } },
  zh = { common: { save: "保存" } },
  source = 'const t = useTranslations("common"); t("save");',
  dynamicExpansions = [],
} = {}) {
  return analyzeCatalog({
    locales: { en, zh },
    sourceFiles: [{ path: "src/example.tsx", source }],
    dynamicExpansions,
  });
}

test("collects static namespaced, rich, server, full translate, and errorKey references", () => {
  const catalog = {
    common: { save: "Save", rich: "Rich", server: "Server" },
    errors: { unknown: "Unknown", rateLimit: "Rate limited" },
  };
  const report = analyze({
    en: catalog,
    zh: catalog,
    source: `
      import { translate } from "@/i18n/translate";
      const t = useTranslations("common");
      t("save");
      t.rich("rich", {});
      const serverT = await getTranslations({ namespace: "common" });
      serverT("server");
      translate("errors.unknown");
      const response = { errorKey: "errors.rateLimit" };
    `,
  });

  assert.deepEqual(report.missingKeys, []);
  assert.deepEqual(report.unusedKeys, []);
  assert.doesNotThrow(() => assertCatalogClean(report));
});

test("detects catalog keys that are not referenced", () => {
  const report = analyze({
    en: { common: { save: "Save", unused: "Unused" } },
    zh: { common: { save: "保存", unused: "未使用" } },
  });

  assert.deepEqual(report.unusedKeys, ["common.unused"]);
  assert.throws(() => assertCatalogClean(report), /unused/i);
});

test("detects locale key mismatches", () => {
  const report = analyze({
    en: { common: { save: "Save", extra: "Extra" } },
    zh: { common: { save: "保存" } },
  });

  assert.deepEqual(report.localeMismatches, ["common.extra: en"]);
  assert.throws(() => assertCatalogClean(report), /locale/i);
});

test("detects references missing from every locale", () => {
  const report = analyze({
    source: 'const t = useTranslations("common"); t("missing");',
  });

  assert.deepEqual(report.missingKeys, ["common.missing: en, zh"]);
  assert.throws(() => assertCatalogClean(report), /missing/i);
});

test("expands a registered finite dynamic call and consumes its registry entry", () => {
  const report = analyze({
    en: { sessionList: { filter: { all: "All", general: "General" } } },
    zh: { sessionList: { filter: { all: "全部", general: "通用" } } },
    source: `
      const t = useTranslations("sessionList");
      t(\`filter.\${option}\`);
    `,
    dynamicExpansions: [
      {
        namespace: "sessionList",
        template: "filter.${option}",
        keys: ["filter.all", "filter.general"],
      },
    ],
  });

  assert.deepEqual(report.unknownDynamicCalls, []);
  assert.deepEqual(report.orphanDynamicExpansions, []);
  assert.deepEqual(report.unusedKeys, []);
  assert.doesNotThrow(() => assertCatalogClean(report));
});

test("rejects an unregistered dynamic translation call", () => {
  const report = analyze({
    source: `
      const t = useTranslations("common");
      t(\`action.\${kind}\`);
    `,
  });

  assert.deepEqual(report.unknownDynamicCalls, ["src/example.tsx:3 common :: action.${kind}"]);
  assert.throws(() => assertCatalogClean(report), /dynamic/i);
});

test("rejects a translator whose namespace is only known at runtime", () => {
  const report = analyze({
    source: `
      const t = useTranslations(namespace);
      t("save");
    `,
  });

  assert.deepEqual(report.unknownDynamicCalls, ["src/example.tsx:2 useTranslations :: namespace"]);
  assert.throws(() => assertCatalogClean(report), /dynamic/i);
});

test("rejects a dynamic expansion that has no matching source call", () => {
  const report = analyze({
    dynamicExpansions: [
      {
        namespace: "common",
        template: "action.${kind}",
        keys: ["action.save"],
      },
    ],
  });

  assert.deepEqual(report.orphanDynamicExpansions, ["common :: action.${kind}"]);
  assert.throws(() => assertCatalogClean(report), /orphan/i);
});

test("detects hardcoded JSX text and user-facing attributes", () => {
  const report = analyze({
    source: `
      export function Example() {
        return <section><button>Save now</button><input placeholder="Your name" /></section>;
      }
    `,
  });

  assert.deepEqual(report.hardcodedFindings, [
    "src/example.tsx:3 JSX text: Save now",
    "src/example.tsx:3 placeholder: Your name",
  ]);
  assert.throws(() => assertCatalogClean(report), /hardcoded/i);
});

test("semantically excludes code and elements explicitly marked as non-translatable", () => {
  const report = analyze({
    source: `
      const t = useTranslations("common");
      export function Example() {
        return <><button>{t("save")}</button><code>request_id</code><span translate="no">Google</span><input translate="no" placeholder="user@example.com" /></>;
      }
    `,
  });

  assert.deepEqual(report.hardcodedFindings, []);
  assert.doesNotThrow(() => assertCatalogClean(report));
});

test("resolves a translator passed with an explicit single-namespace type", () => {
  const report = analyze({
    source: `
      function label(t: ReturnType<typeof useTranslations<"common">>) {
        return t("save");
      }
    `,
  });

  assert.deepEqual(report.unusedKeys, []);
  assert.doesNotThrow(() => assertCatalogClean(report));
});
