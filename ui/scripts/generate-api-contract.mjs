import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptsDir = path.dirname(fileURLToPath(import.meta.url));
const uiRoot = path.resolve(scriptsDir, "..");
const repositoryRoot = path.resolve(uiRoot, "..");
const apiRoot = path.join(repositoryRoot, "api");
const exporter = path.join(scriptsDir, "export-openapi.py");
const artifact = path.join(uiRoot, "src/lib/api/generated/schema.d.ts");
const openapiTypescript = path.join(uiRoot, "node_modules/.bin/openapi-typescript");

const mode = process.argv[2];
if (!new Set(["--write", "--check"]).has(mode)) {
  console.error("Usage: generate-api-contract.mjs --write|--check");
  process.exit(2);
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? uiRoot,
    encoding: "utf8",
    env: {
      ...process.env,
      ENV: "test",
      UV_CACHE_DIR:
        process.env.UV_CACHE_DIR ?? path.join(os.tmpdir(), "opencitadel-openapi-uv-cache"),
    },
  });
  if (result.status !== 0) {
    process.stderr.write(result.stdout ?? "");
    process.stderr.write(result.stderr ?? "");
    process.exit(result.status ?? 1);
  }
}

const temporaryDirectory = mkdtempSync(path.join(os.tmpdir(), "opencitadel-openapi-"));
try {
  const openapiPath = path.join(temporaryDirectory, "openapi.json");
  const generatedPath = path.join(temporaryDirectory, "schema.d.ts");

  run("uv", ["run", "--frozen", "python", exporter, "--output", openapiPath], { cwd: apiRoot });
  run(openapiTypescript, [openapiPath, "--output", generatedPath]);

  const generated = readFileSync(generatedPath, "utf8").replaceAll("\r\n", "\n");
  if (mode === "--write") {
    mkdirSync(path.dirname(artifact), { recursive: true });
    writeFileSync(artifact, generated);
    console.log(`Generated ${path.relative(repositoryRoot, artifact)}`);
    process.exit(0);
  }

  if (!existsSync(artifact) || readFileSync(artifact, "utf8") !== generated) {
    console.error(
      "Generated API contract is stale. Run `npm run api:generate` and include the result.",
    );
    process.exit(1);
  }
  console.log("Generated API contract is current.");
} finally {
  rmSync(temporaryDirectory, { recursive: true, force: true });
}
