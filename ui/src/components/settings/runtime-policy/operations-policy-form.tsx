"use client";

import { useTranslations } from "next-intl";

import { FieldDescription, FieldGroup, FieldLegend, FieldSet } from "@/components/ui/field";

import type { OperationsPolicy } from "@/lib/api/runtime-policies";

import {
  PolicyField,
  type PolicyGroupDefinition,
  readPolicyPath,
  writePolicyPath,
} from "./policy-field";

const GC_FIELDS = (prefix: string) => [
  { path: `${prefix}.enabled`, type: "boolean" as const },
  { path: `${prefix}.retention_count`, type: "number" as const, min: 0, max: 10000 },
  { path: `${prefix}.retention_min_days`, type: "number" as const, min: 0, max: 36500 },
  { path: `${prefix}.batch_size`, type: "number" as const, min: 1, max: 500 },
];

const GROUPS: readonly PolicyGroupDefinition[] = [
  {
    key: "traffic",
    fields: [
      { path: "traffic.rate_limit_enabled", type: "boolean" },
      { path: "traffic.requests_per_minute", type: "number", min: 1, max: 100000 },
      { path: "traffic.session_stream_interval_seconds", type: "number", min: 1, max: 3600 },
    ],
  },
  {
    key: "scheduler",
    fields: [
      { path: "scheduler.enabled", type: "boolean" },
      { path: "scheduler.poll_interval_seconds", type: "number", min: 0.1, max: 3600, step: 0.1 },
      { path: "scheduler.max_concurrent_jobs", type: "number", min: 1, max: 1000 },
      { path: "scheduler.leader_lease_seconds", type: "number", min: 1, max: 3600 },
      {
        path: "scheduler.webhook_idempotency_ttl_seconds",
        type: "number",
        min: 1,
        max: 604800,
      },
    ],
  },
  {
    key: "patrol",
    fields: [
      { path: "patrol.admission", type: "enum", options: ["accepting", "paused"] },
      {
        path: "patrol.remediation",
        type: "enum",
        options: ["disabled", "propose_only", "enabled"],
      },
    ],
  },
  {
    key: "sandbox",
    fields: [
      { path: "sandbox.ttl_minutes", type: "number", min: 1, max: 10080 },
      { path: "sandbox.cleanup_interval_seconds", type: "number", min: 1, max: 3600 },
      { path: "sandbox.memory_limit", type: "string" },
      { path: "sandbox.cpu_limit", type: "number", min: 0.1, max: 128, step: 0.1 },
      { path: "sandbox.pids_limit", type: "number", min: 16, max: 32768 },
      { path: "sandbox.pool_enabled", type: "boolean" },
      { path: "sandbox.pool_size", type: "number", min: 0, max: 100 },
      { path: "sandbox.idle_timeout_minutes", type: "number", min: 1, max: 1440 },
      {
        path: "sandbox.warmup_retry_interval_seconds",
        type: "number",
        min: 0.05,
        max: 60,
        step: 0.05,
      },
      { path: "sandbox.warmup_max_retries", type: "number", min: 1, max: 1000 },
      { path: "sandbox.max_sandboxes_per_node", type: "number", min: 1, max: 1000 },
      { path: "sandbox.max_dynamic_sandboxes_global", type: "number", min: 0, max: 100000 },
      {
        path: "sandbox.admission_min_host_available_mb",
        type: "number",
        min: 0,
        max: 1048576,
      },
      {
        path: "sandbox.admission_reclaim_target_mb",
        type: "number",
        min: 0,
        max: 1048576,
      },
      {
        path: "sandbox.admission_poll_interval_seconds",
        type: "number",
        min: 0.05,
        max: 300,
        step: 0.05,
      },
      {
        path: "sandbox.admission_settle_seconds",
        type: "number",
        min: 0,
        max: 3600,
        step: 0.1,
      },
      { path: "sandbox.admission_reclaim_enabled", type: "boolean" },
      { path: "sandbox.reclaim_leader_lease_seconds", type: "number", min: 1, max: 3600 },
    ],
  },
  {
    key: "resource_gc",
    fields: GC_FIELDS("resource_gc.knowledge_base"),
  },
  {
    key: "patrol_retention",
    fields: [
      { path: "patrol_retention.run_days", type: "number", min: 1, max: 90 },
      { path: "patrol_retention.finding_days", type: "number", min: 1, max: 90 },
      { path: "patrol_retention.collector_evidence_days", type: "number", min: 1, max: 90 },
      { path: "patrol_retention.cleanup_batch_size", type: "number", min: 1, max: 1000 },
    ],
  },
  {
    key: "source_access",
    fields: [
      { path: "source_access.url_allowlist", type: "string-list" },
      { path: "source_access.url_denylist", type: "string-list" },
    ],
  },
];

export function OperationsPolicyForm({
  policy,
  disabled,
  onChange,
}: {
  policy: OperationsPolicy;
  disabled?: boolean;
  onChange: (policy: OperationsPolicy) => void;
}) {
  const t = useTranslations("runtimePolicy");

  return (
    <FieldGroup className="gap-6">
      {GROUPS.map((group) => (
        <FieldSet key={group.key} className="rounded-lg border p-4">
          <FieldLegend>
            {t(`groups.operations.${group.key}` as Parameters<typeof t>[0])}
          </FieldLegend>
          <FieldDescription>
            {t(`groupDescriptions.operations.${group.key}` as Parameters<typeof t>[0])}
          </FieldDescription>
          {group.fields.map((definition) => (
            <PolicyField
              key={definition.path}
              definition={definition}
              label={t(`fields.${definition.path}` as Parameters<typeof t>[0])}
              value={readPolicyPath(policy, definition.path)}
              disabled={disabled}
              onChange={(value) => onChange(writePolicyPath(policy, definition.path, value))}
            />
          ))}
        </FieldSet>
      ))}
    </FieldGroup>
  );
}
