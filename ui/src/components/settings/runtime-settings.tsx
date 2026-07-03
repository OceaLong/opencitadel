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

type RuntimeSettingsProps = {
  isAdmin: boolean;
};

const RUNTIME_SECTIONS: AppConfigSection[] = ["feature_flags", "scheduler", "server"];

const SERVER_VISIBLE_KEYS = new Set([
  "cors_origins",
  "rate_limit_enabled",
  "rate_limit_per_minute",
  "sessions_stream_interval_seconds",
  "marketplace_max_upload_bytes",
]);

const SERVER_READONLY_KEYS = new Set(["cors_origins"]);

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

  const fieldLabel = (section: AppConfigSection, key: string) => {
    const labelKey = `fields.${section}.${key}` as Parameters<typeof t>[0];
    try {
      return t(labelKey);
    } catch {
      return key;
    }
  };

  const fieldDescription = (section: AppConfigSection, key: string) => {
    const descKey = `descriptions.${section}.${key}` as Parameters<typeof t>[0];
    try {
      return t(descKey);
    } catch {
      return undefined;
    }
  };

  const loadSection = useCallback(async (section: AppConfigSection) => {
    setLoading(true);
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
    void loadSection(activeSection);
  }, [activeSection, loadSection]);

  const handleSave = async () => {
    if (!isAdmin) return;
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
      await loadSection(activeSection);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("rollbackFailed"));
    }
  };

  const updateField = (key: string, value: unknown) => {
    setPayload((prev) => ({ ...prev, [key]: value }));
  };

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

      {loading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="text-muted-foreground size-6 animate-spin" />
        </div>
      ) : (
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
            {visibleEntries(activeSection, payload).map(([key, value]) => {
              const description = fieldDescription(activeSection, key);
              const readOnlyField =
                !isAdmin || (activeSection === "server" && SERVER_READONLY_KEYS.has(key));

              if (typeof value === "object" && value !== null) {
                return (
                  <Field key={key}>
                    <FieldLabel>{fieldLabel(activeSection, key)}</FieldLabel>
                    {description ? <FieldDescription>{description}</FieldDescription> : null}
                    <Input
                      value={JSON.stringify(value)}
                      readOnly={readOnlyField}
                      disabled={readOnlyField}
                      onChange={(e) => {
                        try {
                          updateField(key, JSON.parse(e.target.value));
                        } catch {
                          // keep editing
                        }
                      }}
                    />
                  </Field>
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
      )}

      {isAdmin && (
        <div className="flex items-center gap-2">
          <Button type="button" onClick={handleSave} disabled={saving || loading}>
            {saving && <Loader2 className="animate-spin" />}
            {tCommon("save")}
          </Button>
        </div>
      )}

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
