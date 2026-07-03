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

const GLOBAL_SECTIONS: AppConfigSection[] = [
  "feature_flags",
  "worker",
  "sandbox",
  "scheduler",
  "server",
  "streams",
  "observability",
];

export function RuntimeSettings({ isAdmin }: RuntimeSettingsProps) {
  const t = useTranslations("settings");
  const [activeSection, setActiveSection] = useState<AppConfigSection>("feature_flags");
  const [payload, setPayload] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [revisions, setRevisions] = useState<AppConfigRevision[]>([]);

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
      toast.error(err instanceof Error ? err.message : "加载配置失败");
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    void loadSection(activeSection);
  }, [activeSection, loadSection]);

  const handleSave = async () => {
    if (!isAdmin) return;
    setSaving(true);
    try {
      await configApi.updateSection(activeSection, payload);
      toast.success("配置已保存并热生效");
      await loadSection(activeSection);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleRollback = async (revisionId: string) => {
    try {
      await configApi.rollbackRevision(revisionId);
      toast.success("已回滚配置");
      await loadSection(activeSection);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "回滚失败");
    }
  };

  const updateField = (key: string, value: unknown) => {
    setPayload((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="space-y-4 px-1">
      <div className="flex flex-wrap gap-2">
        {GLOBAL_SECTIONS.map((section) => (
          <Button
            key={section}
            type="button"
            size="sm"
            variant={activeSection === section ? "default" : "outline"}
            onClick={() => setActiveSection(section)}
          >
            {section}
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
            <FieldLegend className="text-foreground text-lg font-semibold">{activeSection}</FieldLegend>
            {activeSection === "server" && (
              <FieldDescription className="text-xs text-amber-600">
                cors_origins 修改后需重启 API 进程才能生效，其余 server 字段可热更新。
              </FieldDescription>
            )}
            {Object.entries(payload).map(([key, value]) => {
              if (typeof value === "object" && value !== null) {
                return (
                  <Field key={key}>
                    <FieldLabel>{key}</FieldLabel>
                    <Input
                      value={JSON.stringify(value)}
                      readOnly={!isAdmin}
                      disabled={!isAdmin}
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
                    <FieldLabel>{key}</FieldLabel>
                    <Switch
                      checked={value}
                      disabled={!isAdmin}
                      onCheckedChange={(checked) => updateField(key, checked)}
                    />
                  </Field>
                );
              }
              return (
                <Field key={key}>
                  <FieldLabel>{key}</FieldLabel>
                  <Input
                    type={typeof value === "number" ? "number" : "text"}
                    value={value === null || value === undefined ? "" : String(value)}
                    readOnly={!isAdmin}
                    disabled={!isAdmin}
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
            {t("save")}
          </Button>
        </div>
      )}

      {isAdmin && revisions.length > 0 && (
        <FieldSet>
          <FieldLegend className="text-sm font-semibold">最近版本</FieldLegend>
          <div className="space-y-2">
            {revisions.map((rev) => (
              <div key={rev.id} className="flex items-center justify-between rounded border px-3 py-2 text-xs">
                <span>{rev.created_at} · {rev.note || rev.changed_by || "update"}</span>
                <Button type="button" size="xs" variant="outline" onClick={() => handleRollback(rev.id)}>
                  回滚
                </Button>
              </div>
            ))}
          </div>
        </FieldSet>
      )}
    </div>
  );
}
