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

type KBChunkConfig = {
  parent_max_chars?: number;
  child_max_chars?: number;
  overlap?: number;
};

type KBRetrievalConfig = {
  vector_top_k?: number;
  bm25_top_k?: number;
  rrf_k?: number;
  final_top_k?: number;
};

type KBRerankConfig = {
  enabled?: boolean;
  provider?: string;
  base_url?: string | null;
  api_key_env?: string | null;
  model?: string | null;
  timeout_seconds?: number;
};

type KBGraphRAGConfig = {
  enabled?: boolean;
  max_parent_chunks_per_doc?: number;
  concurrency?: number;
};

type KBOCRConfig = {
  mode?: string;
  max_pages?: number;
};

type KBDocumentConfig = {
  max_bytes?: number;
  max_pages?: number;
};

type KBConnectorsConfig = {
  confluence_base_url?: string | null;
  feishu_base_url?: string | null;
  url_allowlist?: string[];
  url_denylist?: string[];
};

export type KnowledgeBaseRuntimeConfig = {
  vector_enabled?: boolean;
  chunk?: KBChunkConfig;
  retrieval?: KBRetrievalConfig;
  rerank?: KBRerankConfig;
  graphrag?: KBGraphRAGConfig;
  ocr?: KBOCRConfig;
  document?: KBDocumentConfig;
  connectors?: KBConnectorsConfig;
};

type KnowledgeBaseRuntimeFormProps = {
  isAdmin: boolean;
};

const CHUNK_FIELDS: Record<string, ConfigFieldSchema> = {
  parent_max_chars: { type: "number", min: 101, max: 20000 },
  child_max_chars: { type: "number", min: 51, max: 5000 },
  overlap: { type: "number", min: 0, max: 1000 },
};

const RETRIEVAL_FIELDS: Record<string, ConfigFieldSchema> = {
  vector_top_k: { type: "number", min: 1, max: 100 },
  bm25_top_k: { type: "number", min: 1, max: 100 },
  rrf_k: { type: "number", min: 1, max: 1000 },
  final_top_k: { type: "number", min: 1, max: 30 },
};

const RERANK_FIELDS: Record<string, ConfigFieldSchema> = {
  enabled: { type: "boolean" },
  provider: { type: "enum", options: ["llm", "api"] },
  base_url: { type: "string", nullable: true },
  api_key_env: { type: "string", nullable: true },
  model: { type: "string", nullable: true },
  timeout_seconds: { type: "number", min: 0.1, max: 180, step: 0.1 },
};

const GRAPHRAG_FIELDS: Record<string, ConfigFieldSchema> = {
  enabled: { type: "boolean" },
  max_parent_chunks_per_doc: { type: "number", min: 0, max: 5000 },
  concurrency: { type: "number", min: 1, max: 20 },
};

const OCR_FIELDS: Record<string, ConfigFieldSchema> = {
  mode: { type: "enum", options: ["vision_llm", "rapidocr", "off"] },
  max_pages: { type: "number", min: 0, max: 1000 },
};

const DOCUMENT_FIELDS: Record<string, ConfigFieldSchema> = {
  max_bytes: { type: "number", min: 1, max: 524288000 },
  max_pages: { type: "number", min: 1, max: 10000 },
};

const CONNECTORS_FIELDS: Record<string, ConfigFieldSchema> = {
  confluence_base_url: { type: "string", nullable: true },
  feishu_base_url: { type: "string", nullable: true },
  url_allowlist: { type: "string[]" },
  url_denylist: { type: "string[]" },
};

export function KnowledgeBaseRuntimeForm({ isAdmin }: KnowledgeBaseRuntimeFormProps) {
  const t = useTranslations("settingsRuntime");
  const tCommon = useTranslations("common");
  const [payload, setPayload] = useState<KnowledgeBaseRuntimeConfig>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const fieldLabel = (key: string) => {
    const labelKey = `fields.knowledge_base.${key}` as Parameters<typeof t>[0];
    return t.has(labelKey) ? t(labelKey) : key;
  };

  const fieldDescription = (key: string) => {
    const descKey = `descriptions.knowledge_base.${key}` as Parameters<typeof t>[0];
    return t.has(descKey) ? t(descKey) : undefined;
  };

  const groupTitle = (key: string) => {
    const groupKey = `groups.knowledge_base.${key}` as Parameters<typeof t>[0];
    return t.has(groupKey) ? t(groupKey) : key;
  };

  const groupDescription = (key: string) => {
    const descKey = `groupDescriptions.knowledge_base.${key}` as Parameters<typeof t>[0];
    return t.has(descKey) ? t(descKey) : undefined;
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await configApi.getSection<KnowledgeBaseRuntimeConfig>("knowledge_base");
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

  const updateTopLevel = (key: keyof KnowledgeBaseRuntimeConfig, value: unknown) => {
    setPayload((prev) => ({ ...prev, [key]: value }));
  };

  const updateNested = <G extends keyof KnowledgeBaseRuntimeConfig>(
    group: G,
    key: string,
    value: unknown,
  ) => {
    setPayload((prev) => ({
      ...prev,
      [group]: {
        ...((prev[group] as Record<string, unknown> | undefined) ?? {}),
        [key]: value,
      },
    }));
  };

  const handleSave = async () => {
    if (!isAdmin) return;
    setSaving(true);
    try {
      await configApi.updateSection("knowledge_base", payload as Record<string, unknown>);
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

  const renderGroupFields = <G extends keyof KnowledgeBaseRuntimeConfig>(
    group: G,
    fields: Record<string, ConfigFieldSchema>,
  ) => {
    const groupValue = (payload[group] as Record<string, unknown> | undefined) ?? {};
    return Object.entries(fields).map(([key, schema]) => (
      <ConfigField
        key={key}
        label={fieldLabel(key)}
        description={fieldDescription(key)}
        schema={schema}
        value={groupValue[key]}
        readOnly={!isAdmin}
        onChange={(value) => updateNested(group, key, value)}
      />
    ));
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="text-muted-foreground size-6 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <FieldGroup>
        <FieldSet>
          <FieldLegend className="text-foreground text-lg font-semibold">
            {t("sections.knowledge_base")}
          </FieldLegend>
          <FieldDescription>{t("knowledgeBaseSectionDesc")}</FieldDescription>

          <ConfigField
            label={fieldLabel("vector_enabled")}
            description={fieldDescription("vector_enabled")}
            schema={{ type: "boolean" }}
            value={payload.vector_enabled ?? true}
            readOnly={!isAdmin}
            onChange={(value) => updateTopLevel("vector_enabled", value)}
          />

          <ConfigGroupCard title={groupTitle("chunk")} description={groupDescription("chunk")}>
            {renderGroupFields("chunk", CHUNK_FIELDS)}
          </ConfigGroupCard>

          <ConfigGroupCard title={groupTitle("retrieval")} description={groupDescription("retrieval")}>
            {renderGroupFields("retrieval", RETRIEVAL_FIELDS)}
          </ConfigGroupCard>

          <ConfigGroupCard title={groupTitle("rerank")} description={groupDescription("rerank")}>
            {renderGroupFields("rerank", RERANK_FIELDS)}
          </ConfigGroupCard>

          <ConfigGroupCard title={groupTitle("graphrag")} description={groupDescription("graphrag")}>
            {renderGroupFields("graphrag", GRAPHRAG_FIELDS)}
          </ConfigGroupCard>

          <ConfigGroupCard title={groupTitle("ocr")} description={groupDescription("ocr")}>
            {renderGroupFields("ocr", OCR_FIELDS)}
          </ConfigGroupCard>

          <ConfigGroupCard title={groupTitle("document")} description={groupDescription("document")}>
            {renderGroupFields("document", DOCUMENT_FIELDS)}
          </ConfigGroupCard>

          <ConfigGroupCard title={groupTitle("connectors")} description={groupDescription("connectors")}>
            {renderGroupFields("connectors", CONNECTORS_FIELDS)}
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
