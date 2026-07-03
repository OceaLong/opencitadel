import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..");

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

const zh = JSON.parse(fs.readFileSync(path.join(root, "messages/zh.json"), "utf8"));
const en = JSON.parse(fs.readFileSync(path.join(root, "messages/en.json"), "utf8"));
const zhKeys = new Set(flatten(zh));
const enKeys = new Set(flatten(en));
const onlyZh = [...zhKeys].filter((k) => !enKeys.has(k)).sort();
const onlyEn = [...enKeys].filter((k) => !zhKeys.has(k)).sort();

if (onlyZh.length || onlyEn.length) {
  console.error("i18n key mismatch:");
  if (onlyZh.length) console.error("  only in zh.json:", onlyZh.join(", "));
  if (onlyEn.length) console.error("  only in en.json:", onlyEn.join(", "));
  process.exit(1);
}

console.log(`i18n keys aligned: ${zhKeys.size} keys in zh.json and en.json`);
