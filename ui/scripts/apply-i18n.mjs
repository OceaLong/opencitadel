#!/usr/bin/env node
/**
 * Applies i18n replacements to tsx files using flat key mappings.
 * Run after updating scripts/i18n-replacements.json
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..");
const replacementsPath = path.join(__dirname, "i18n-replacements.json");

if (!fs.existsSync(replacementsPath)) {
  console.log("No i18n-replacements.json found, skipping apply");
  process.exit(0);
}

const config = JSON.parse(fs.readFileSync(replacementsPath, "utf8"));

for (const [fileRel, rules] of Object.entries(config.files ?? {})) {
  const filePath = path.join(root, fileRel);
  if (!fs.existsSync(filePath)) continue;
  let content = fs.readFileSync(filePath, "utf8");
  let changed = false;

  for (const rule of rules) {
    if (content.includes(rule.from)) {
      content = content.split(rule.from).join(rule.to);
      changed = true;
    }
  }

  if (changed) {
    fs.writeFileSync(filePath, content);
    console.log("Updated", fileRel);
  }
}

console.log("Done");
