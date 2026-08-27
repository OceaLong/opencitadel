import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const scriptsDir = path.dirname(fileURLToPath(import.meta.url));
const uiRoot = path.resolve(scriptsDir, "..");
const generator = path.join(scriptsDir, "generate-api-contract.mjs");
const artifact = path.join(uiRoot, "src/lib/api/generated/schema.d.ts");

function run(mode) {
  return spawnSync(process.execPath, [generator, mode], {
    cwd: uiRoot,
    encoding: "utf8",
  });
}

test("generation is byte-stable and check mode detects drift", () => {
  const first = run("--write");
  assert.equal(first.status, 0, first.stderr || first.stdout);
  const expected = readFileSync(artifact, "utf8");

  const second = run("--write");
  assert.equal(second.status, 0, second.stderr || second.stdout);
  assert.equal(readFileSync(artifact, "utf8"), expected);

  const clean = run("--check");
  assert.equal(clean.status, 0, clean.stderr || clean.stdout);

  writeFileSync(artifact, `${expected}\n// deliberate drift\n`);
  try {
    const drifted = run("--check");
    assert.notEqual(drifted.status, 0);
  } finally {
    writeFileSync(artifact, expected);
  }
});

test("generated contract contains only the inference control-plane paths", () => {
  const result = run("--write");
  assert.equal(result.status, 0, result.stderr || result.stdout);
  const contract = readFileSync(artifact, "utf8");

  assert.match(contract, /\/api\/inference\/endpoints/);
  assert.match(contract, /\/api\/inference\/models/);
  assert.match(contract, /\/api\/inference\/bindings\/\{purpose\}/);
  assert.match(contract, /\/api\/capabilities/);
  assert.doesNotMatch(contract, /\/api\/llm-(?:endpoints|models)/);
  assert.doesNotMatch(contract, /\/api\/llm\/status/);
});
