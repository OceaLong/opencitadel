"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { FieldDescription, FieldGroup, FieldLegend, FieldSet } from "@/components/ui/field";
import { configApi } from "@/lib/api/config";

import { ConfigField } from "./config-field";
import { ConfigGroupCard } from "./config-group-card";
import type { ConfigFieldSchema } from "./config-schema";

type EmbeddingConfig = {
  provider?: string;
  model?: string;
  base_url?: string;
  timeout_seconds?: number;
};

export type MemoryRuntimeConfig = {
  recall_limit?: number;
  auto_extract_enabled?: boolean;
  vector_enabled?: boolean;
  compact_strategy?: string;
  compact_token_threshold?: number;
  compact_keep_recent?: number;
  compact_tool_content_max_chars?: number;
  compact_always_on_step_boundary?: boolean;
  compact_rule_trigger_threshold?: number;
  tool_output_offload_enabled?: boolean;
  tool_output_offload_threshold_chars?: number;
  embedding?: EmbeddingConfig;
};

type MemoryRuntimeFormProps = {
  isAdmin: boolean;
};

const FLAT_FIELDS: Record<string, ConfigFieldSchema> = {
  recall_limit: { type: "number", min: 1, max: 100 },
  auto_extract_enabled: { type: "boolean" },
  vector_enabled: { type: "boolean" },
  compact_strategy: { type: "enum", options: ["rule", "llm", "hybrid"] },
  compact_token_threshold: { type: "number", min: 1000, max: 200000 },
  compact_keep_recent: { type: "number", min: 1, max: 100 },
  compact_tool_content_max_chars: { type: "number", min: 1000, max: 100000 },
  compact_always_on_step_boundary: { type: "boolean" },
  compact_rule_trigger_threshold: { type: "number", min: 1000, max: 200000 },
  tool_output_offload_enabled: { type: "boolean" },
  tool_output_offload_threshold_chars: { type: "number", min: 500, max: 100000 },
};

const EMBEDDING_FIELDS: Record<string, ConfigFieldSchema> = {
  provider: { type: "string" },
  model: { type: "string" },
  base_url: { type: "string" },
  timeout_seconds: { type: "number", min: 0.1, max: 300, step: 0.1 },
};

export function MemoryRuntimeForm({ isAdmin }: MemoryRuntimeFormProps) {
  const t = useTranslations("settingsRuntime");
  const tCommon = useTranslations("common");
  const [payload, setPayload] = useState<MemoryRuntimeConfig>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const fieldLabel = (key: string) => {
    const labelKey = `fields.memory.${key}` as Parameters<typeof t>[0];
    return t.has(labelKey) ? t(labelKey) : key;
  };

  const fieldDescription = (key: string) => {
    const descKey = `descriptions.memory.${key}` as Parameters<typeof t>[0];
    return t.has(descKey) ? t(descKey) : undefined;
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await configApi.getSection<MemoryRuntimeConfig>("memory");
      setPayload(data ?? {});
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const updateField = (key: keyof MemoryRuntimeConfig, value: unknown) => {
    setPayload((prev) => ({ ...prev, [key]: value }));
  };

  const updateEmbedding = (key: string, value: unknown) => {
    setPayload((prev) => ({
      ...prev,
      embedding: {
        ...(prev.embedding ?? {}),
        [key]: value,
      },
    }));
  };

  const handleSave = async () => {
    if (!isAdmin) return;
    setSaving(true);
    try {
      await configApi.updateSection("memory", payload as Record<string, unknown>);
      toast.success(t("saveSuccess"));
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleResetOverride = async () => {
    try {
      await configApi.deleteUserOverride();
      toast.success(t("resetOverrideSuccess"));
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("resetOverrideFailed"));
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="text-muted-foreground size-6 animate-spin" />
      </div>
    );
  }

  const embeddingValue = payload.embedding ?? {};

  return (
    <div className="space-y-4">
      <FieldGroup>
        <FieldSet>
          <FieldLegend className="text-foreground text-lg font-semibold">
            {t("sections.memory")}
          </FieldLegend>
          <FieldDescription>{t("memorySectionDesc")}</FieldDescription>

          {Object.entries(FLAT_FIELDS).map(([key, schema]) => (
            <ConfigField
              key={key}
              label={fieldLabel(key)}
              description={fieldDescription(key)}
              schema={schema}
              value={payload[key as keyof MemoryRuntimeConfig]}
              readOnly={!isAdmin}
              onChange={(value) => updateField(key as keyof MemoryRuntimeConfig, value)}
            />
          ))}

          <ConfigGroupCard
            title={t("groups.memory.embedding")}
            description={t.has("groupDescriptions.memory.embedding") ? t("groupDescriptions.memory.embedding") : undefined}
          >
            {Object.entries(EMBEDDING_FIELDS).map(([key, schema]) => (
              <ConfigField
                key={key}
                label={fieldLabel(`embedding.${key}`)}
                description={fieldDescription(`embedding.${key}`)}
                schema={schema}
                value={embeddingValue[key as keyof EmbeddingConfig]}
                readOnly={!isAdmin}
                onChange={(value) => updateEmbedding(key, value)}
              />
            ))}
          </ConfigGroupCard>
        </FieldSet>
      </FieldGroup>

      <div className="flex flex-wrap gap-2">
        {isAdmin ? (
          <Button type="button" onClick={() => void handleSave()} disabled={saving || loading}>
            {saving && <Loader2 className="mr-1 size-4 animate-spin" />}
            {tCommon("save")}
          </Button>
        ) : null}
        <Button type="button" variant="outline" onClick={() => void handleResetOverride()}>
          {t("resetUserOverride")}
        </Button>
      </div>
    </div>
  );
}
