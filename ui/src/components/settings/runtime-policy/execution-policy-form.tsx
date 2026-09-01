"use client";

import { useTranslations } from "next-intl";

import { FieldDescription, FieldGroup, FieldLegend, FieldSet } from "@/components/ui/field";

import type { ExecutionPolicy } from "@/lib/api/runtime-policies";

import {
  PolicyField,
  type PolicyGroupDefinition,
  readPolicyPath,
  writePolicyPath,
} from "./policy-field";

const GROUPS: readonly PolicyGroupDefinition[] = [
  {
    key: "agent",
    fields: [
      { path: "agent.max_iterations", type: "number", min: 1, max: 100 },
      { path: "agent.max_retries", type: "number", min: 0, max: 10 },
    ],
  },
  {
    key: "model_resilience",
    fields: [
      { path: "model_resilience.enabled", type: "boolean" },
      { path: "model_resilience.fallback_enabled", type: "boolean" },
      { path: "model_resilience.allow_cross_provider_fallback", type: "boolean" },
      { path: "model_resilience.fallback_on_quota_exceeded", type: "boolean" },
      { path: "model_resilience.allow_cross_provider_fallback_on_quota", type: "boolean" },
      { path: "model_resilience.max_attempts_per_call", type: "number", min: 1, max: 10 },
      {
        path: "model_resilience.max_call_budget_seconds",
        type: "number",
        min: 0.01,
        max: 600,
        step: 0.1,
      },
      { path: "model_resilience.breaker_window_seconds", type: "number", min: 1, max: 3600 },
      { path: "model_resilience.breaker_error_threshold", type: "number", min: 1, max: 100 },
      {
        path: "model_resilience.breaker_open_ttl_seconds",
        type: "number",
        min: 1,
        max: 3600,
      },
      {
        path: "model_resilience.breaker_halfopen_probe_timeout_seconds",
        type: "number",
        min: 1,
        max: 60,
      },
      { path: "model_resilience.fast_fail_on_open_circuit", type: "boolean" },
    ],
  },
  {
    key: "activity",
    fields: [
      { path: "activity.tool_timeout_seconds", type: "number", min: 1, max: 86400 },
      { path: "activity.mcp_connect_timeout_seconds", type: "number", min: 1, max: 3600 },
    ],
  },
  {
    key: "memory",
    fields: [
      { path: "memory.recall_limit", type: "number", min: 1, max: 100 },
      { path: "memory.vector_enabled", type: "boolean" },
    ],
  },
  {
    key: "knowledge_base",
    fields: [
      { path: "knowledge_base.vector_enabled", type: "boolean" },
      { path: "knowledge_base.chunk.parent_max_chars", type: "number", min: 101, max: 20000 },
      { path: "knowledge_base.chunk.child_max_chars", type: "number", min: 51, max: 5000 },
      { path: "knowledge_base.chunk.overlap", type: "number", min: 0, max: 1000 },
      { path: "knowledge_base.retrieval.vector_top_k", type: "number", min: 1, max: 100 },
      { path: "knowledge_base.retrieval.bm25_top_k", type: "number", min: 1, max: 100 },
      { path: "knowledge_base.retrieval.rrf_k", type: "number", min: 1, max: 1000 },
      { path: "knowledge_base.retrieval.final_top_k", type: "number", min: 1, max: 30 },
      { path: "knowledge_base.rerank.enabled", type: "boolean" },
      {
        path: "knowledge_base.rerank.timeout_seconds",
        type: "number",
        min: 0.01,
        max: 180,
        step: 0.1,
      },
      { path: "knowledge_base.graphrag.enabled", type: "boolean" },
      {
        path: "knowledge_base.graphrag.max_parent_chunks_per_doc",
        type: "number",
        min: 0,
        max: 5000,
      },
      { path: "knowledge_base.graphrag.concurrency", type: "number", min: 1, max: 20 },
      { path: "knowledge_base.graphrag.max_chunks", type: "number", min: 1, max: 1000000 },
      { path: "knowledge_base.graphrag.max_llm_calls", type: "number", min: 1, max: 1000000 },
      { path: "knowledge_base.graphrag.max_tokens", type: "number", min: 1, max: 1000000000 },
      {
        path: "knowledge_base.graphrag.deadline_seconds",
        type: "number",
        min: 0.01,
        max: 3600,
        step: 0.1,
      },
      {
        path: "knowledge_base.ocr.mode",
        type: "enum",
        options: ["vision_llm", "rapidocr", "off"],
      },
      { path: "knowledge_base.ocr.max_pages", type: "number", min: 0, max: 1000 },
      { path: "knowledge_base.document.max_bytes", type: "number", min: 1, max: 524288000 },
      { path: "knowledge_base.document.max_pages", type: "number", min: 1, max: 10000 },
    ],
  },
];

export function ExecutionPolicyForm({
  policy,
  disabled,
  onChange,
}: {
  policy: ExecutionPolicy;
  disabled?: boolean;
  onChange: (policy: ExecutionPolicy) => void;
}) {
  const t = useTranslations("runtimePolicy");

  return (
    <FieldGroup className="gap-6">
      {GROUPS.map((group) => (
        <FieldSet key={group.key} className="rounded-lg border p-4">
          <FieldLegend>{t(`groups.execution.${group.key}` as Parameters<typeof t>[0])}</FieldLegend>
          <FieldDescription>
            {t(`groupDescriptions.execution.${group.key}` as Parameters<typeof t>[0])}
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
