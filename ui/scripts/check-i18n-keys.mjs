import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { analyzeCatalog, assertCatalogClean } from "./i18n/catalog-checker.mjs";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(scriptDirectory, "..");
const repositoryRoot = path.join(root, "..");
const sourceRoot = path.join(root, "src");
const runtimeKeyManifest = JSON.parse(
  fs.readFileSync(path.join(repositoryRoot, "contracts/i18n-runtime-keys.json"), "utf8"),
);

const RUNTIME_POLICY_GROUPS = {
  execution: ["agent", "model_resilience", "activity", "memory", "knowledge_base", "codebase"],
  operations: [
    "traffic",
    "scheduler",
    "patrol",
    "sandbox",
    "resource_gc",
    "patrol_retention",
    "source_access",
  ],
};

const RUNTIME_POLICY_FIELD_PATHS = [
  "agent.max_iterations",
  "agent.max_retries",
  "model_resilience.enabled",
  "model_resilience.fallback_enabled",
  "model_resilience.allow_cross_provider_fallback",
  "model_resilience.fallback_on_quota_exceeded",
  "model_resilience.allow_cross_provider_fallback_on_quota",
  "model_resilience.max_attempts_per_call",
  "model_resilience.max_call_budget_seconds",
  "model_resilience.breaker_window_seconds",
  "model_resilience.breaker_error_threshold",
  "model_resilience.breaker_open_ttl_seconds",
  "model_resilience.breaker_halfopen_probe_timeout_seconds",
  "model_resilience.fast_fail_on_open_circuit",
  "activity.tool_timeout_seconds",
  "activity.mcp_connect_timeout_seconds",
  "memory.recall_limit",
  "memory.vector_enabled",
  "knowledge_base.vector_enabled",
  "knowledge_base.chunk.parent_max_chars",
  "knowledge_base.chunk.child_max_chars",
  "knowledge_base.chunk.overlap",
  "knowledge_base.retrieval.vector_top_k",
  "knowledge_base.retrieval.bm25_top_k",
  "knowledge_base.retrieval.rrf_k",
  "knowledge_base.retrieval.final_top_k",
  "knowledge_base.rerank.enabled",
  "knowledge_base.rerank.timeout_seconds",
  "knowledge_base.graphrag.enabled",
  "knowledge_base.graphrag.max_parent_chunks_per_doc",
  "knowledge_base.graphrag.concurrency",
  "knowledge_base.graphrag.max_chunks",
  "knowledge_base.graphrag.max_llm_calls",
  "knowledge_base.graphrag.max_tokens",
  "knowledge_base.graphrag.deadline_seconds",
  "knowledge_base.ocr.mode",
  "knowledge_base.ocr.max_pages",
  "knowledge_base.document.max_bytes",
  "knowledge_base.document.max_pages",
  "codebase.vector_enabled",
  "codebase.analysis.max_file_size_bytes",
  "codebase.analysis.max_files",
  "codebase.analysis.chunk_max_chars",
  "codebase.analysis.source_read_batch_size",
  "codebase.retrieval.fetch_multiplier",
  "codebase.retrieval.rrf_k",
  "codebase.retrieval.final_top_k",
  "traffic.rate_limit_enabled",
  "traffic.requests_per_minute",
  "traffic.session_stream_interval_seconds",
  "scheduler.enabled",
  "scheduler.poll_interval_seconds",
  "scheduler.max_concurrent_jobs",
  "scheduler.leader_lease_seconds",
  "scheduler.webhook_idempotency_ttl_seconds",
  "patrol.admission",
  "patrol.remediation",
  "sandbox.ttl_minutes",
  "sandbox.cleanup_interval_seconds",
  "sandbox.memory_limit",
  "sandbox.cpu_limit",
  "sandbox.pids_limit",
  "sandbox.pool_enabled",
  "sandbox.pool_size",
  "sandbox.idle_timeout_minutes",
  "sandbox.warmup_retry_interval_seconds",
  "sandbox.warmup_max_retries",
  "sandbox.max_sandboxes_per_node",
  "sandbox.max_dynamic_sandboxes_global",
  "sandbox.admission_min_host_available_mb",
  "sandbox.admission_reclaim_target_mb",
  "sandbox.admission_poll_interval_seconds",
  "sandbox.admission_settle_seconds",
  "sandbox.admission_reclaim_enabled",
  "sandbox.reclaim_leader_lease_seconds",
  "resource_gc.knowledge_base.enabled",
  "resource_gc.knowledge_base.retention_count",
  "resource_gc.knowledge_base.retention_min_days",
  "resource_gc.knowledge_base.batch_size",
  "resource_gc.codebase.enabled",
  "resource_gc.codebase.retention_count",
  "resource_gc.codebase.retention_min_days",
  "resource_gc.codebase.batch_size",
  "patrol_retention.run_days",
  "patrol_retention.finding_days",
  "patrol_retention.collector_evidence_days",
  "patrol_retention.cleanup_batch_size",
  "source_access.url_allowlist",
  "source_access.url_denylist",
];

const DYNAMIC_EXPANSIONS = [
  ...Object.entries(RUNTIME_POLICY_GROUPS).flatMap(([kind, groups]) => [
    {
      namespace: "runtimePolicy",
      template: `groups.${kind}.${"${group.key}"}`,
      keys: groups.map((group) => `groups.${kind}.${group}`),
    },
    {
      namespace: "runtimePolicy",
      template: `groupDescriptions.${kind}.${"${group.key}"}`,
      keys: groups.map((group) => `groupDescriptions.${kind}.${group}`),
    },
  ]),
  {
    namespace: "runtimePolicy",
    template: "fields.${definition.path}",
    keys: RUNTIME_POLICY_FIELD_PATHS.map((path) => `fields.${path}`),
  },
  {
    namespace: "sessionList",
    template: "filter.${option}",
    keys: ["filter.all", "filter.general", "filter.codebase", "filter.knowledge", "filter.hybrid"],
  },
  {
    namespace: "sessionList",
    template: "filter.${contextKind}",
    keys: ["filter.codebase", "filter.knowledge", "filter.hybrid"],
  },
  {
    namespace: "codebase",
    template: "artifacts.${key}",
    keys: [
      "artifacts.architecture",
      "artifacts.dataFlow",
      "artifacts.moduleDir",
      "artifacts.callChain",
      "artifacts.flowchart",
      "artifacts.overview",
    ],
  },
  {
    namespace: "settingsInference",
    template: "purpose_${purpose}",
    keys: ["purpose_chat", "purpose_embedding", "purpose_rerank"],
  },
  {
    namespace: "automation",
    template: "labelKey",
    keys: ["webhookUrlLabel", "tokenLabel", "secretLabel"],
  },
  {
    namespace: "automation",
    template: "ariaKey",
    keys: ["copyWebhookUrlAria", "copyTokenAria", "copySecretAria"],
  },
  {
    namespace: "automation",
    template: 'TRIGGER_LABEL[form.trigger_type ?? "interval"]',
    keys: ["triggerInterval", "triggerCron", "triggerWebhook"],
  },
  {
    namespace: "admin",
    template: "option.labelKey",
    keys: ["timeRange7d", "timeRange30d", "timeRange90d", "timeRangeAll"],
  },
  {
    namespace: "adminNav",
    template: "labelKey",
    keys: [
      "overview",
      "users",
      "teams",
      "invitations",
      "audit",
      "governance",
      "evidence",
      "complianceReport",
    ],
  },
  {
    namespace: "adminNav",
    template: "adminItem.labelKey",
    keys: [
      "overview",
      "users",
      "teams",
      "invitations",
      "audit",
      "governance",
      "evidence",
      "complianceReport",
    ],
  },
  {
    namespace: "nav",
    template: "module.key",
    keys: ["chat", "patrol", "automation", "knowledge", "codebase", "admin"],
  },
  {
    namespace: "nav",
    template: "activeModule.key",
    keys: ["chat", "patrol", "automation", "knowledge", "codebase", "admin"],
  },
  {
    namespace: "codebase",
    template: "CODEBASE_STATUS_LABEL_KEYS[cb.status]",
    keys: [
      "status.pending",
      "status.materializing",
      "status.analyzing",
      "status.indexing",
      "status.generating",
      "status.ready",
      "status.failed",
    ],
  },
  {
    namespace: "knowledge",
    template: "KB_STATUS_LABEL_KEYS[kb.status]",
    keys: [
      "status.pending",
      "status.parsing",
      "status.chunking",
      "status.indexing",
      "status.graph_building",
      "status.ready",
      "status.failed",
    ],
  },
  {
    namespace: "settings",
    template: "errorKey",
    keys: [
      "mcpUrlRequired",
      "mcpUrlInvalidScheme",
      "mcpParamValueRequiredWhenUndecryptable",
      "mcpCommandRequired",
    ],
  },
  {
    namespace: "settings",
    template: 'transport === "stdio" ? "mcpCommandRequired" : "mcpUrlRequired"',
    keys: ["mcpCommandRequired", "mcpUrlRequired"],
  },
  {
    namespace: "settings",
    template: "validationError",
    keys: ["mcpUrlRequired", "mcpUrlInvalidScheme"],
  },
  {
    namespace: "settings",
    template: "menu.labelKey",
    keys: ["common", "agent", "inference", "skills", "memory", "integrations", "runtime"],
  },
  {
    template: "body.error_key",
    keys: runtimeKeyManifest.apiErrorKeys,
  },
  {
    template: "item.i18n_key",
    keys: runtimeKeyManifest.notificationKeys,
  },
  {
    template: "WEEKDAY_KEYS[date.getDay()]",
    keys: [
      "common.dates.weekdaySun",
      "common.dates.weekdayMon",
      "common.dates.weekdayTue",
      "common.dates.weekdayWed",
      "common.dates.weekdayThu",
      "common.dates.weekdayFri",
      "common.dates.weekdaySat",
    ],
  },
];

function walkSources(directory, files = []) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolutePath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      walkSources(absolutePath, files);
    } else if (/\.[cm]?[jt]sx?$/.test(entry.name)) {
      files.push({
        path: path.relative(root, absolutePath),
        source: fs.readFileSync(absolutePath, "utf8"),
      });
    }
  }
  return files;
}

const locales = Object.fromEntries(
  ["en", "zh"].map((locale) => [
    locale,
    JSON.parse(fs.readFileSync(path.join(root, `messages/${locale}.json`), "utf8")),
  ]),
);
const report = analyzeCatalog({
  locales,
  sourceFiles: walkSources(sourceRoot),
  dynamicExpansions: DYNAMIC_EXPANSIONS,
});

try {
  assertCatalogClean(report);
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
}

if (process.exitCode !== 1) {
  console.log("i18n catalog is aligned, fully referenced, and free of hardcoded UI strings");
}
