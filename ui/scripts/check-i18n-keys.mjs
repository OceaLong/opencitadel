import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..");
const srcDir = path.join(root, "src");

function flatten(obj, prefix = "") {
  const keys = [];
  for (const [key, value] of Object.entries(obj)) {
    const full = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      keys.push(...flatten(value, full));
    } else {
      keys.push(full);
    }
  }
  return keys;
}

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(fullPath, out);
    } else if (/\.(tsx?|jsx?)$/.test(entry.name)) {
      out.push(fullPath);
    }
  }
  return out;
}

/** Expand known dynamic t(`foo.${var}`) patterns used in the codebase. */
// Keep marketplace app keys aligned with APP_I18N_KEYS in app-registry.tsx
const MARKETPLACE_APP_I18N_KEYS = [
  "nutritionAnalysis",
  "consumptionCalculator",
  "smartTranslation",
  "promptLab",
  "qrGenerator",
  "devToolbox",
  "secretGenerator",
  "documentConverter",
  "watermarkTool",
];

const DYNAMIC_EXPANSIONS = [
  { prefix: "sessionList.filter.", values: ["all", "general", "codebase", "knowledge", "hybrid"] },
  { prefix: "operatorScope.gateProfile.", suffix: ".title", values: ["loose", "standard", "strict"] },
  { prefix: "operatorScope.gateProfile.", suffix: ".description", values: ["loose", "standard", "strict"] },
  { prefix: "codebase.artifacts.", values: ["architecture", "dataFlow", "moduleDir", "callChain", "flowchart", "overview"] },
  { prefix: "marketplaceApps.promptLab.styleHints.", values: ["agent", "analysis", "writing"] },
  { prefix: "sessionMemory.roles.", values: ["system", "user", "assistant", "tool", "unknown"] },
  ...MARKETPLACE_APP_I18N_KEYS.flatMap((appKey) => [
    { prefix: `marketplace.apps.${appKey}.`, values: ["name", "description", "tags", "examples"] },
  ]),
  {
    prefix: "settingsRuntime.sections.",
    values: ["feature_flags", "scheduler", "server"],
  },
];

function collectUsedKeys() {
  const used = new Set();
  const unresolvedDynamic = new Set();

  for (const file of walk(srcDir)) {
    const src = fs.readFileSync(file, "utf8");
    const varNs = {};

    for (const re of [
      /(?:const|let|var)\s+(\w+)\s*=\s*useTranslations\(\s*"([^"]+)"\s*\)/g,
      /(?:const|let|var)\s+(\w+)\s*=\s*await\s+getTranslations\(\s*(?:\{[^}]*namespace:\s*)?"([^"]+)"/g,
    ]) {
      let match;
      while ((match = re.exec(src))) {
        varNs[match[1]] = match[2];
      }
    }

    for (const [varName, namespace] of Object.entries(varNs)) {
      const callRe = new RegExp(`\\b${varName}\\(\\s*("([^"]*)"|\`([^\`]*)\`)`, "g");
      let call;
      while ((call = callRe.exec(src))) {
        if (call[2] !== undefined) {
          used.add(`${namespace}.${call[2]}`);
        } else {
          unresolvedDynamic.add(`${namespace} :: ${call[3]}`);
        }
      }
    }
  }

  for (const { prefix, suffix = "", values } of DYNAMIC_EXPANSIONS) {
    for (const value of values) {
      used.add(`${prefix}${value}${suffix}`);
    }
  }

  return { used, unresolvedDynamic };
}

const zh = JSON.parse(fs.readFileSync(path.join(root, "messages/zh.json"), "utf8"));
const en = JSON.parse(fs.readFileSync(path.join(root, "messages/en.json"), "utf8"));
const zhKeys = new Set(flatten(zh));
const enKeys = new Set(flatten(en));

let failed = false;

const onlyZh = [...zhKeys].filter((k) => !enKeys.has(k)).sort();
const onlyEn = [...enKeys].filter((k) => !zhKeys.has(k)).sort();

if (onlyZh.length || onlyEn.length) {
  failed = true;
  console.error("i18n locale key mismatch:");
  if (onlyZh.length) console.error("  only in zh.json:", onlyZh.join(", "));
  if (onlyEn.length) console.error("  only in en.json:", onlyEn.join(", "));
}

const { used, unresolvedDynamic } = collectUsedKeys();
const missingFromEn = [...used].filter((k) => !enKeys.has(k)).sort();
const missingFromZh = [...used].filter((k) => !zhKeys.has(k)).sort();

if (missingFromEn.length || missingFromZh.length) {
  failed = true;
  console.error("i18n keys used in code but missing from message files:");
  if (missingFromEn.length) {
    console.error(`  missing from en.json (${missingFromEn.length}):`);
    console.error("   ", missingFromEn.join(", "));
  }
  if (missingFromZh.length) {
    console.error(`  missing from zh.json (${missingFromZh.length}):`);
    console.error("   ", missingFromZh.join(", "));
  }
}

const knownDynamicPatterns = new Set([
  "sessionList :: filter.${option}",
  "sessionList :: filter.${contextKind}",
  "operatorScope :: gateProfile.${profile}.title",
  "operatorScope :: gateProfile.${profile}.description",
  "codebase :: artifacts.${key}",
  "marketplaceApps.promptLab :: styleHints.${style}",
  "sessionMemory :: roles.${role}",
  "marketplace.apps :: ${appKey}.name",
  "marketplace.apps :: ${appKey}.description",
  "marketplace.apps :: ${appKey}.tags",
  "marketplace.apps :: ${appKey}.examples",
  "marketplace :: categoryAll",
  "settingsRuntime :: sections.${activeSection}",
  "settingsRuntime :: sections.${section}",
]);

const unknownDynamic = [...unresolvedDynamic].filter((p) => !knownDynamicPatterns.has(p)).sort();
if (unknownDynamic.length) {
  failed = true;
  console.error("Unhandled dynamic i18n key patterns (add to DYNAMIC_EXPANSIONS or use static keys):");
  for (const pattern of unknownDynamic) {
    console.error("  ", pattern);
  }
}

if (failed) {
  process.exit(1);
}

/** Heuristic scan for likely hardcoded user-facing strings (warnings only). */
const HARDCODED_PATTERNS = [
  {
    name: "toast literal",
    re: /toast\.(?:success|error|info|warning)\(\s*["'`][^"'`]+["'`]/g,
    allow: (line) => line.includes("t(") || line.includes("translate("),
  },
  {
    name: "JSX Chinese text",
    re: />[^<{]*[\u4e00-\u9fff][^<{]*</g,
    allow: (line) => line.trim().startsWith("//") || line.includes("{/*"),
  },
];

const hardcodedFindings = [];
for (const file of walk(srcDir)) {
  const rel = path.relative(root, file);
  if (rel.includes("/components/ui/")) continue;
  const lines = fs.readFileSync(file, "utf8").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    for (const { name, re, allow } of HARDCODED_PATTERNS) {
      if (allow?.(line)) continue;
      re.lastIndex = 0;
      if (re.test(line)) {
        hardcodedFindings.push(`${rel}:${i + 1} [${name}] ${line.trim().slice(0, 120)}`);
      }
    }
  }
}

if (hardcodedFindings.length) {
  console.warn(
    `i18n heuristic: ${hardcodedFindings.length} possible hardcoded string(s) (review manually):`,
  );
  for (const finding of hardcodedFindings.slice(0, 20)) {
    console.warn(" ", finding);
  }
  if (hardcodedFindings.length > 20) {
    console.warn(`  ... and ${hardcodedFindings.length - 20} more`);
  }
}

console.log(
  `i18n OK: ${zhKeys.size} keys aligned; ${used.size} code-referenced keys present in en.json and zh.json`,
);
