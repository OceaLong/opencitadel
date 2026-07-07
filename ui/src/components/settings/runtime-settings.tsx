"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { configApi, type AppConfigSection } from "@/lib/api/config";
import type { AppConfigRevision } from "@/lib/api/types";

import { KnowledgeBaseRuntimeForm } from "./runtime/knowledge-base-runtime-form";
import { MemoryRuntimeForm } from "./runtime/memory-runtime-form";
import { JsonObjectField } from "./runtime/json-object-field";

type RuntimeSettingsProps = {
  isAdmin: boolean;
};

const RUNTIME_SECTIONS: AppConfigSection[] = [
  "feature_flags",
  "scheduler",
  "server",
  "memory",
  "knowledge_base",
  "sandbox",
  "worker",
  "streams",
  "observability",
];

const STRUCTURED_SECTIONS = new Set<AppConfigSection>(["memory", "knowledge_base"]);

const SERVER_VISIBLE_KEYS = new Set([
  "cors_origins",
  "rate_limit_enabled",
  "rate_limit_per_minute",
  "sessions_stream_interval_seconds",
  "marketplace_max_upload_bytes",
]);

const SERVER_READONLY_KEYS = new Set(["cors_origins"]);

const SECTION_DESC_KEYS: Partial<Record<AppConfigSection, Parameters<ReturnType<typeof useTranslations<"settingsRuntime">>>[0]>> = {
  worker: "workerSectionDesc",
  streams: "streamsSectionDesc",
  observability: "observabilitySectionDesc",
  sandbox: "sandboxSectionDesc",
};

function visibleEntries(
  section: AppConfigSection,
  payload: Record<string, unknown>,
): Array<[string, unknown]> {
  const entries = Object.entries(payload);
  if (section !== "server") {
    return entries;
  }
  return entries.filter(([key]) => SERVER_VISIBLE_KEYS.has(key));
}

export function RuntimeSettings({ isAdmin }: RuntimeSettingsProps) {
  const t = useTranslations("settingsRuntime");
  const tCommon = useTranslations("common");
  const [activeSection, setActiveSection] = useState<AppConfigSection>("feature_flags");
  const [payload, setPayload] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [revisions, setRevisions] = useState<AppConfigRevision[]>([]);
  const [jsonFieldErrors, setJsonFieldErrors] = useState<Record<string, boolean>>({});
  const [formReloadKey, setFormReloadKey] = useState(0);

  const fieldLabel = (section: AppConfigSection, key: string) => {
    const labelKey = `fields.${section}.${key}` as Parameters<typeof t>[0];
    return t.has(labelKey) ? t(labelKey) : key;
  };

  const fieldDescription = (section: AppConfigSection, key: string) => {
    const descKey = `descriptions.${section}.${key}` as Parameters<typeof t>[0];
    return t.has(descKey) ? t(descKey) : undefined;
  };

  const loadSection = useCallback(async (section: AppConfigSection) => {
    setLoading(true);
    setJsonFieldErrors({});
    try {
      const data = await configApi.getSection<Record<string, unknown>>(section);
      setPayload(data ?? {});
      if (isAdmin) {
        const revs = await configApi.listRevisions({ scope: "global", limit: 10 });
        setRevisions(revs ?? []);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [isAdmin, t]);

  useEffect(() => {
    if (STRUCTURED_SECTIONS.has(activeSection)) {
      if (isAdmin) {
        void configApi.listRevisions({ scope: "global", limit: 10 }).then((revs) => {
          setRevisions(revs ?? []);
        }).catch(() => {
          setRevisions([]);
        });
      }
      return;
    }
    void loadSection(activeSection);
  }, [activeSection, isAdmin, loadSection]);

  const handleSave = async () => {
    if (!isAdmin) return;
    if (Object.values(jsonFieldErrors).some(Boolean)) {
      toast.error(t("invalidJson"));
      return;
    }
    setSaving(true);
    try {
      await configApi.updateSection(activeSection, payload);
      toast.success(t("saveSuccess"));
      await loadSection(activeSection);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleRollback = async (revisionId: string) => {
    try {
      await configApi.rollbackRevision(revisionId);
      toast.success(t("rollbackSuccess"));
      if (STRUCTURED_SECTIONS.has(activeSection)) {
        setFormReloadKey((key) => key + 1);
        return;
      }
      await loadSection(activeSection);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("rollbackFailed"));
    }
  };

  const handleResetUserOverride = async () => {
    try {
      await configApi.deleteUserOverride();
      toast.success(t("resetOverrideSuccess"));
      if (STRUCTURED_SECTIONS.has(activeSection)) {
        setFormReloadKey((key) => key + 1);
        return;
      }
      await loadSection(activeSection);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("resetOverrideFailed"));
    }
  };

  const updateField = (key: string, value: unknown) => {
    setPayload((prev) => ({ ...prev, [key]: value }));
  };

  const renderGenericForm = () => (
    <FieldGroup>
      <FieldSet>
        <FieldLegend className="text-foreground text-lg font-semibold">
          {t(`sections.${activeSection}` as Parameters<typeof t>[0])}
        </FieldLegend>
        {activeSection === "server" && (
          <FieldDescription className="text-xs text-amber-600">
            {t("corsOriginsReadonlyHint")}
          </FieldDescription>
        )}
        {SECTION_DESC_KEYS[activeSection] ? (
          <FieldDescription>{t(SECTION_DESC_KEYS[activeSection]!)}</FieldDescription>
        ) : null}
        {visibleEntries(activeSection, payload).map(([key, value]) => {
          const description = fieldDescription(activeSection, key);
          const readOnlyField =
            !isAdmin || (activeSection === "server" && SERVER_READONLY_KEYS.has(key));

          if (typeof value === "object" && value !== null && !Array.isArray(value)) {
            return (
              <JsonObjectField
                key={key}
                label={fieldLabel(activeSection, key)}
                description={description}
                value={value}
                readOnly={readOnlyField}
                invalidJsonMessage={t("invalidJsonField")}
                onChange={(next) => updateField(key, next)}
                onValidityChange={(valid) => {
                  setJsonFieldErrors((prev) => ({ ...prev, [key]: !valid }));
                }}
              />
            );
          }
          if (typeof value === "boolean") {
            return (
              <Field key={key} orientation="horizontal">
                <div className="space-y-1">
                  <FieldLabel>{fieldLabel(activeSection, key)}</FieldLabel>
                  {description ? <FieldDescription>{description}</FieldDescription> : null}
                </div>
                <Switch
                  checked={value}
                  disabled={readOnlyField}
                  onCheckedChange={(checked) => updateField(key, checked)}
                />
              </Field>
            );
          }
          return (
            <Field key={key}>
              <FieldLabel>{fieldLabel(activeSection, key)}</FieldLabel>
              {description ? <FieldDescription>{description}</FieldDescription> : null}
              <Input
                type={typeof value === "number" ? "number" : "text"}
                value={value === null || value === undefined ? "" : String(value)}
                readOnly={readOnlyField}
                disabled={readOnlyField}
                onChange={(e) => {
                  const raw = e.target.value;
                  if (typeof value === "number") {
                    updateField(key, raw === "" ? undefined : Number(raw));
                  } else {
                    updateField(key, raw);
                  }
                }}
              />
            </Field>
          );
        })}
      </FieldSet>
    </FieldGroup>
  );

  const showGenericActions = !STRUCTURED_SECTIONS.has(activeSection);

  return (
    <div className="space-y-4 px-1">
      <div className="flex flex-wrap gap-2">
        {RUNTIME_SECTIONS.map((section) => (
          <Button
            key={section}
            type="button"
            size="sm"
            variant={activeSection === section ? "default" : "outline"}
            onClick={() => setActiveSection(section)}
          >
            {t(`sections.${section}` as Parameters<typeof t>[0])}
          </Button>
        ))}
      </div>

      {activeSection === "knowledge_base" ? (
        <KnowledgeBaseRuntimeForm key={`kb-${formReloadKey}`} isAdmin={isAdmin} />
      ) : activeSection === "memory" ? (
        <MemoryRuntimeForm key={`mem-${formReloadKey}`} isAdmin={isAdmin} />
      ) : loading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="text-muted-foreground size-6 animate-spin" />
        </div>
      ) : (
        renderGenericForm()
      )}

      {showGenericActions ? (
        <div className="flex flex-wrap items-center gap-2">
          {isAdmin ? (
            <Button type="button" onClick={handleSave} disabled={saving || loading}>
              {saving && <Loader2 className="animate-spin" />}
              {tCommon("save")}
            </Button>
          ) : null}
          <Button type="button" variant="outline" onClick={() => void handleResetUserOverride()}>
            {t("resetUserOverride")}
          </Button>
        </div>
      ) : null}

      {isAdmin && revisions.length > 0 && (
        <FieldSet>
          <FieldLegend className="text-sm font-semibold">{t("recentRevisions")}</FieldLegend>
          <div className="space-y-2">
            {revisions.map((rev) => (
              <div key={rev.id} className="flex items-center justify-between rounded border px-3 py-2 text-xs">
                <span>{rev.created_at} · {rev.note || rev.changed_by || t("revisionUpdateDefault")}</span>
                <Button type="button" size="xs" variant="outline" onClick={() => handleRollback(rev.id)}>
                  {t("rollback")}
                </Button>
              </div>
            ))}
          </div>
        </FieldSet>
      )}
    </div>
  );
}
