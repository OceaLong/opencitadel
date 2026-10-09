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
  execution: ["agent", "model_resilience", "activity", "memory", "knowledge_base"],
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
  "patrol_retention.run_days",
  "patrol_retention.finding_days",
  "patrol_retention.collector_evidence_days",
  "patrol_retention.cleanup_batch_size",
  "source_access.url_allowlist",
  "source_access.url_denylist",
];

const EVALUATION_STATUS_KEYS = [
  "stateAllocated",
  "statePreparing",
  "stateReady",
  "stateLeased",
  "stateCleaning",
  "stateVerifiedClean",
  "stateQuarantine",
  "stateAccepted",
  "stateAdmitting",
  "stateBlocked",
  "stateBlockedBudget",
  "stateCancelled",
  "stateCancelling",
  "stateClean",
  "stateComplete",
  "stateCompleted",
  "stateCompletedWithErrors",
  "stateCreated",
  "stateFailed",
  "stateIntent",
  "stateMismatch",
  "stateNotRequired",
  "statePending",
  "stateProcessing",
  "stateQueued",
  "stateRejected",
  "stateRunning",
  "stateSkipped",
  "stateSubmitted",
  "stateSucceeded",
  "stateUnknown",
  "stateValidating",
  "stateWaiting",
];

const DYNAMIC_EXPANSIONS = [
  {
    namespace: "analysis",
    template: "diffStatus.${status}",
    keys: [
      "diffStatus.queued",
      "diffStatus.running",
      "diffStatus.complete",
      "diffStatus.partial",
      "diffStatus.failed",
    ],
  },
  {
    namespace: "analysis",
    template: 'index === 0 ? "leftRun" : "rightRun"',
    keys: ["leftRun", "rightRun"],
  },
  {
    namespace: "analysis",
    template:
      'row.status === "suggested"\n                            ? "suggested"\n                            : row.status === "confirmed"\n                              ? "confirmed"\n                              : "unmatched"',
    keys: ["suggested", "confirmed", "unmatched"],
  },
  { namespace: "analysis", template: 'i === 0 ? "before" : "after"', keys: ["before", "after"] },
  {
    namespace: "analysis",
    template: 'source.source_kind === "filter" ? "createExportSnapshot" : "exportRevision"',
    keys: ["createExportSnapshot", "exportRevision"],
  },
  {
    namespace: "analysis",
    template: "exportStatus.${job.status}",
    keys: [
      "exportStatus.queued",
      "exportStatus.running",
      "exportStatus.ready",
      "exportStatus.failed",
      "exportStatus.invalidated",
      "exportStatus.expired",
    ],
  },
  {
    namespace: "analysis",
    template: "key",
    keys: [
      "start",
      "end",
      "session",
      "model_revision",
      "configuration_revision",
      "family",
      "tool",
      "status",
      "mode",
      "purpose",
      "batch_id",
    ],
  },
  {
    namespace: "analysis",
    template: 'kind === "success" ? "successTrend" : "latencyTrend"',
    keys: ["successTrend", "latencyTrend"],
  },
  {
    namespace: "analysis",
    template: 'showAll ? "topTen" : "showAll"',
    keys: ["topTen", "showAll"],
  },
  {
    namespace: "evaluations",
    template: "evaluationStatusKey(lease.state)",
    keys: EVALUATION_STATUS_KEYS,
  },
  {
    namespace: "evaluations",
    template: 'lease.reusable ? "environmentReusable" : "environmentNotReusable"',
    keys: ["environmentReusable", "environmentNotReusable"],
  },
  {
    namespace: "evaluations",
    template: "environmentPhaseKey(phase)",
    keys: [
      "environmentPhasePrepare",
      "environmentPhaseReset",
      "environmentPhaseVerifyReady",
      "environmentPhaseCleanup",
      "environmentPhaseVerifyClean",
      "stateUnknown",
    ],
  },
  {
    namespace: "evaluations",
    template: "evaluationStatusKey(reviewStatus)",
    keys: EVALUATION_STATUS_KEYS,
  },
  {
    namespace: "evaluations",
    template: "evaluationStatusKey(batch.status)",
    keys: EVALUATION_STATUS_KEYS,
  },
  {
    namespace: "evaluations",
    template: "evaluationStatusKey(key)",
    keys: EVALUATION_STATUS_KEYS,
  },
  {
    namespace: "evaluations",
    template: 'evaluationStatusKey(batch.review_status ?? "")',
    keys: EVALUATION_STATUS_KEYS,
  },
  {
    namespace: "evaluations",
    template: 'evaluationStatusKey(batch.cleanup_status ?? "")',
    keys: EVALUATION_STATUS_KEYS,
  },
  {
    namespace: "evaluations",
    template: "evaluationStatusKey(receipt.status)",
    keys: EVALUATION_STATUS_KEYS,
  },
  {
    namespace: "evaluations",
    template: "evaluationStatusKey(attempt.status)",
    keys: EVALUATION_STATUS_KEYS,
  },
  {
    namespace: "evaluations",
    template:
      'source === "rule" ? "ruleScores" : source === "model" ? "modelScores" : "humanScores"',
    keys: ["ruleScores", "modelScores", "humanScores"],
  },
  {
    namespace: "evaluations",
    template: 'kind === "subject_usage" ? "subjectCost" : "judgeCost"',
    keys: ["subjectCost", "judgeCost"],
  },
  {
    namespace: "evaluations",
    template: 'a.kind === "original" ? "originalBudget" : "additionalBudget"',
    keys: ["originalBudget", "additionalBudget"],
  },
  {
    namespace: "evaluations",
    template: 'row.score.value ? "rulePassed" : "ruleFailed"',
    keys: ["rulePassed", "ruleFailed"],
  },
  {
    namespace: "evaluations",
    template: "state",
    keys: ["matrixFailed", "matrixPendingScore", "matrixNotRun", "missingScore", "matrixScored"],
  },
  {
    namespace: "evaluations",
    template: 'preview.input_status ?? "provided"',
    keys: ["provided", "admitted", "sanitized", "unavailable", "edited"],
  },
  {
    namespace: "evaluations",
    template: 'c.input_status ?? "provided"',
    keys: ["provided", "admitted", "sanitized", "unavailable", "edited"],
  },
  {
    namespace: "evaluations",
    template: 'c.reference_confirmed ? "confirmed" : "unconfirmed"',
    keys: ["confirmed", "unconfirmed"],
  },
  { namespace: "evaluations", template: 'fixed ? "inspect" : "edit"', keys: ["inspect", "edit"] },
  {
    namespace: "evaluations",
    template: 'filter ? "filteredEmpty" : "empty"',
    keys: ["filteredEmpty", "empty"],
  },
  {
    namespace: "evaluations",
    template: 'selected ? "editCase" : "newCase"',
    keys: ["editCase", "newCase"],
  },
  {
    namespace: "evaluations",
    template: 'selected.input_status ?? "provided"',
    keys: ["provided", "admitted", "sanitized", "unavailable", "edited"],
  },
  {
    namespace: "evaluations",
    template: "key",
    keys: [
      "concurrency",
      "memory_mb",
      "cpu_millis",
      "pids",
      "timeout_seconds",
      "repeat",
      "seed",
      "token_budget",
      "money_budget",
      "case_timeout_seconds",
      "batch_timeout_seconds",
      "subject_concurrency",
      "judge_concurrency",
      "environment_concurrency",
      "max_results",
    ],
  },
  {
    namespace: "evaluations",
    template: "error",
    keys: ["conflict", "denied", "unavailable", "failed"],
  },
  {
    namespace: "evaluations",
    template: "kind",
    keys: [
      "datasets",
      "configs",
      "rubrics",
      "suites",
      "recordings",
      "environments",
      "added",
      "replaced",
      "removed",
    ],
  },
  {
    namespace: "evaluations",
    template: "k",
    keys: [
      "text_exact",
      "text_normalized",
      "json_schema",
      "jsonpath",
      "required_fields",
      "citations",
      "artifact",
    ],
  },
  {
    namespace: "evaluations",
    template: "p",
    keys: ["optional", "required", "required_when_applicable"],
  },
  {
    namespace: "evaluations",
    template: "job.status",
    keys: ["queued", "running", "ready", "failed"],
  },
  {
    namespace: "evaluations",
    template: "result.price_coverage",
    keys: ["unknown", "partial", "complete"],
  },
  {
    namespace: "evaluations",
    template: 'result.environment_ready ? "ready" : "unavailable"',
    keys: ["ready", "unavailable"],
  },
  {
    namespace: "evaluations",
    template: 'expired ? "expired" : preview.revision !== revision ? "conflict" : "importErrors"',
    keys: ["expired", "conflict", "importErrors"],
  },
  {
    namespace: "evaluations",
    template: 'item.qualified ? "qualified" : "unavailable"',
    keys: ["qualified", "unavailable"],
  },
  {
    namespace: "evaluations",
    template: 'fixed.version_unpinned ? "versionUnpinned" : "versionPinned"',
    keys: ["versionUnpinned", "versionPinned"],
  },
  {
    namespace: "evaluations",
    template: 'task.pending ? "loading" : "empty"',
    keys: ["loading", "empty"],
  },
  {
    namespace: "executionTrace",
    template: 'expanded ? "collapse" : "expand"',
    keys: ["collapse", "expand"],
  },
  {
    namespace: "executionTrace",
    template: "warning",
    keys: ["cycle", "pending", "incomplete", "missing"],
  },
  {
    namespace: "executionTrace",
    template: 'complete ? "hidden" : "hiddenPartial"',
    keys: ["hidden", "hiddenPartial"],
  },
  {
    namespace: "executionTrace",
    template: 'complete ? "complete" : "partial"',
    keys: ["complete", "partial"],
  },
  {
    namespace: "executionTrace",
    template: "field",
    keys: ["kind", "status", "tool", "retry", "approval"],
  },
  {
    namespace: "executionTrace",
    template: "kinds.${value}",
    keys: [
      "kinds.model",
      "kinds.tool",
      "kinds.activity",
      "kinds.phase",
      "kinds.approval",
      "kinds.clarification",
      "kinds.unknown",
    ],
  },
  {
    namespace: "executionTrace",
    template: 'exhausted ? (complete ? "complete" : "incomplete") : "partial"',
    keys: ["complete", "incomplete", "partial"],
  },
  {
    namespace: "executionTrace",
    template: 'active ? "emptyFilter" : "empty"',
    keys: ["emptyFilter", "empty"],
  },
  {
    namespace: "executionWorkbench",
    template: "status.${row.step.status}",
    keys: [
      "status.new",
      "status.queued",
      "status.running",
      "status.waiting",
      "status.completed",
      "status.failed",
      "status.cancelled",
      "status.deferred",
      "status.unknown",
    ],
  },
  {
    namespace: "executionWorkbench",
    template: "status.${value}",
    keys: [
      "status.new",
      "status.queued",
      "status.running",
      "status.waiting",
      "status.completed",
      "status.failed",
      "status.cancelled",
      "status.deferred",
      "status.unknown",
    ],
  },
  {
    namespace: "executionWorkbench",
    template: "status.${run.status}",
    keys: [
      "status.new",
      "status.queued",
      "status.running",
      "status.waiting",
      "status.completed",
      "status.failed",
      "status.cancelled",
      "status.deferred",
      "status.unknown",
    ],
  },
  {
    namespace: "executionWorkbench",
    template: "status.${step.status}",
    keys: [
      "status.new",
      "status.queued",
      "status.running",
      "status.waiting",
      "status.completed",
      "status.failed",
      "status.cancelled",
      "status.deferred",
      "status.unknown",
    ],
  },
  {
    namespace: "executionWorkbench",
    template: "loadState.${workbench.loadState}",
    keys: [
      "loadState.idle",
      "loadState.loading",
      "loadState.refreshing",
      "loadState.ready",
      "loadState.forbidden",
      "loadState.conflict",
      "loadState.unavailable",
      "loadState.rebuilding",
      "loadState.error",
    ],
  },
  {
    namespace: "executionWorkbench",
    template: "listState.${runState}",
    keys: [
      "listState.loading",
      "listState.ready",
      "listState.empty",
      "listState.updating",
      "listState.forbidden",
      "listState.error",
    ],
  },
  {
    namespace: "executionWorkbench",
    template: "availability.${artifact.availability}",
    keys: [
      "availability.available",
      "availability.unavailable",
      "availability.pending",
      "availability.unknown",
    ],
  },
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
    keys: ["filter.all", "filter.general", "filter.knowledge"],
  },
  {
    namespace: "sessionList",
    template: "filter.${contextKind}",
    keys: ["filter.knowledge"],
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
    keys: ["chat", "patrol", "automation", "knowledge", "admin", "evaluations", "analysis"],
  },
  {
    namespace: "nav",
    template: "activeModule.key",
    keys: ["chat", "patrol", "automation", "knowledge", "admin", "evaluations", "analysis"],
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
