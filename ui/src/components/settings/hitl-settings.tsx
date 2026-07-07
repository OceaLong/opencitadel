"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { configApi } from "@/lib/api/config";

type GateProfileSettings = {
  first_visit_domain_gate?: boolean;
  tool_gate_call_level_enabled?: boolean;
  selective_critical_only?: boolean;
};

type HitlConfig = {
  plan_gate_enabled?: boolean;
  tool_gate_task_level_enabled?: boolean;
  tool_gate_call_level_enabled?: boolean;
  tool_gate_risk_list?: string[];
  critical_action_patterns?: string[];
  gate_profiles?: Record<string, GateProfileSettings>;
  takeover_timeout_minutes?: number;
};

const GATE_PROFILE_NAMES = ["loose", "standard", "strict"] as const;

type HitlSettingsProps = {
  isAdmin: boolean;
};

function linesToList(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function listToLines(items?: string[]): string {
  return (items ?? []).join("\n");
}

export function HitlSettings({ isAdmin }: HitlSettingsProps) {
  const t = useTranslations("settingsHitl");
  const tCommon = useTranslations("common");
  const [payload, setPayload] = useState<HitlConfig>({});
  const [riskListText, setRiskListText] = useState("");
  const [criticalPatternsText, setCriticalPatternsText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await configApi.getSection<HitlConfig>("hitl");
      setPayload(data ?? {});
      setRiskListText(listToLines(data?.tool_gate_risk_list));
      setCriticalPatternsText(listToLines(data?.critical_action_patterns));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const updateField = <K extends keyof HitlConfig>(key: K, value: HitlConfig[K]) => {
    setPayload((prev) => ({ ...prev, [key]: value }));
  };

  const updateGateProfile = (
    profile: string,
    key: keyof GateProfileSettings,
    value: boolean,
  ) => {
    setPayload((prev) => ({
      ...prev,
      gate_profiles: {
        ...(prev.gate_profiles ?? {}),
        [profile]: {
          ...(prev.gate_profiles?.[profile] ?? {}),
          [key]: value,
        },
      },
    }));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const next: HitlConfig = {
        ...payload,
        tool_gate_risk_list: linesToList(riskListText),
        critical_action_patterns: linesToList(criticalPatternsText),
      };
      await configApi.updateSection("hitl", next);
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

  const profileValue = (profile: string, key: keyof GateProfileSettings) =>
    payload.gate_profiles?.[profile]?.[key] ?? false;

  return (
    <div className="space-y-4 px-1">
      {loading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="text-muted-foreground size-6 animate-spin" />
        </div>
      ) : (
        <FieldGroup>
          <FieldSet>
            <FieldLegend className="text-foreground text-lg font-semibold">{t("title")}</FieldLegend>
            <FieldDescription>{t("description")}</FieldDescription>

            <Field orientation="horizontal">
              <div className="space-y-1">
                <FieldLabel>{t("planGateEnabled")}</FieldLabel>
                <FieldDescription>{t("planGateEnabledDesc")}</FieldDescription>
              </div>
              <Switch
                checked={payload.plan_gate_enabled ?? true}
                disabled={!isAdmin}
                onCheckedChange={(checked) => updateField("plan_gate_enabled", checked)}
              />
            </Field>

            <Field orientation="horizontal">
              <div className="space-y-1">
                <FieldLabel>{t("toolGateTaskLevel")}</FieldLabel>
                <FieldDescription>{t("toolGateTaskLevelDesc")}</FieldDescription>
              </div>
              <Switch
                checked={payload.tool_gate_task_level_enabled ?? true}
                disabled={!isAdmin}
                onCheckedChange={(checked) => updateField("tool_gate_task_level_enabled", checked)}
              />
            </Field>

            <Field orientation="horizontal">
              <div className="space-y-1">
                <FieldLabel>{t("toolGateCallLevel")}</FieldLabel>
                <FieldDescription>{t("toolGateCallLevelDesc")}</FieldDescription>
              </div>
              <Switch
                checked={payload.tool_gate_call_level_enabled ?? false}
                disabled={!isAdmin}
                onCheckedChange={(checked) => updateField("tool_gate_call_level_enabled", checked)}
              />
            </Field>

            <Field>
              <FieldLabel>{t("toolGateRiskList")}</FieldLabel>
              <FieldDescription>{t("toolGateRiskListDesc")}</FieldDescription>
              <Textarea
                rows={4}
                value={riskListText}
                readOnly={!isAdmin}
                disabled={!isAdmin}
                onChange={(e) => setRiskListText(e.target.value)}
              />
            </Field>

            <Field>
              <FieldLabel>{t("criticalActionPatterns")}</FieldLabel>
              <FieldDescription>{t("criticalActionPatternsDesc")}</FieldDescription>
              <Textarea
                rows={4}
                value={criticalPatternsText}
                readOnly={!isAdmin}
                disabled={!isAdmin}
                onChange={(e) => setCriticalPatternsText(e.target.value)}
              />
            </Field>

            <Field>
              <FieldLabel>{t("takeoverTimeout")}</FieldLabel>
              <Input
                type="number"
                min={1}
                max={120}
                value={payload.takeover_timeout_minutes ?? 30}
                readOnly={!isAdmin}
                disabled={!isAdmin}
                onChange={(e) =>
                  updateField("takeover_timeout_minutes", Number(e.target.value) || 30)
                }
              />
            </Field>
          </FieldSet>

          <FieldSet>
            <FieldLegend className="text-base font-semibold">{t("gateProfilesTitle")}</FieldLegend>
            {GATE_PROFILE_NAMES.map((profile) => (
              <div key={profile} className="border-border/70 mb-4 space-y-3 rounded-xl border p-3">
                <p className="text-sm font-medium">{t(`gateProfile.${profile}`)}</p>
                <Field orientation="horizontal">
                  <FieldLabel>{t("firstVisitDomainGate")}</FieldLabel>
                  <Switch
                    checked={profileValue(profile, "first_visit_domain_gate")}
                    disabled={!isAdmin}
                    onCheckedChange={(checked) =>
                      updateGateProfile(profile, "first_visit_domain_gate", checked)
                    }
                  />
                </Field>
                <Field orientation="horizontal">
                  <FieldLabel>{t("profileToolGateCallLevel")}</FieldLabel>
                  <Switch
                    checked={profileValue(profile, "tool_gate_call_level_enabled")}
                    disabled={!isAdmin}
                    onCheckedChange={(checked) =>
                      updateGateProfile(profile, "tool_gate_call_level_enabled", checked)
                    }
                  />
                </Field>
                <Field orientation="horizontal">
                  <FieldLabel>{t("selectiveCriticalOnly")}</FieldLabel>
                  <Switch
                    checked={profileValue(profile, "selective_critical_only")}
                    disabled={!isAdmin}
                    onCheckedChange={(checked) =>
                      updateGateProfile(profile, "selective_critical_only", checked)
                    }
                  />
                </Field>
              </div>
            ))}
          </FieldSet>
        </FieldGroup>
      )}

      <div className="flex flex-wrap gap-2">
        <Button type="button" onClick={() => void handleSave()} disabled={saving || loading}>
          {saving && <Loader2 className="mr-1 size-4 animate-spin" />}
          {tCommon("save")}
        </Button>
        <Button type="button" variant="outline" onClick={() => void handleResetOverride()}>
          {t("resetUserOverride")}
        </Button>
      </div>
    </div>
  );
}
