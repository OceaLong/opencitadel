"use client";

import { useTranslations } from "next-intl";
import type { Dispatch, SetStateAction } from "react";

import { ContextSelector } from "@/components/context-selector";
import { SessionModelPicker } from "@/components/session-model-picker";
import { SessionSkillPicker } from "@/components/session-skill-picker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Sheet, SheetContent, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import type { CreateScheduledJobParams, ScheduledJob } from "@/lib/api/types";

const TRIGGER_LABEL: Record<string, string> = {
  interval: "triggerInterval",
  cron: "triggerCron",
  webhook: "triggerWebhook",
};

/** Default values for the "new job" form. */
export const EMPTY_JOB_FORM: CreateScheduledJobParams = {
  name: "",
  trigger_type: "interval",
  trigger_spec: "3600",
  prompt_template: "",
  enabled: true,
  notify_channels: [],
  operator_domains: [],
};

/** Maps an existing job onto the editable form shape (used when opening the edit sheet). */
export function jobToFormValues(job: ScheduledJob): CreateScheduledJobParams {
  return {
    name: job.name,
    trigger_type: job.trigger_type as CreateScheduledJobParams["trigger_type"],
    trigger_spec: job.trigger_spec,
    prompt_template: job.prompt_template,
    skill_id: job.skill_id,
    model_id: job.model_id,
    codebase_id: job.codebase_id,
    knowledge_base_id: job.knowledge_base_id,
    notify_channels: job.notify_channels ?? [],
    operator_scope: job.operator_scope ?? null,
    operator_domains: job.operator_domains ?? [],
    gate_profile: job.gate_profile ?? "standard",
    enabled: job.enabled,
  };
}

type JobFormSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editingJob: ScheduledJob | null;
  form: CreateScheduledJobParams;
  onFormChange: Dispatch<SetStateAction<CreateScheduledJobParams>>;
  onSubmit: () => void;
  submitting: boolean;
};

/**
 * Right-side Sheet container for the job create/edit form. Form state stays
 * controlled on the page (`form` / `onFormChange`) — this component only
 * renders the fields and hands submit/cancel back up, so page.tsx retains a
 * single source of truth for validation and API calls.
 */
export function JobFormSheet({
  open,
  onOpenChange,
  editingJob,
  form,
  onFormChange,
  onSubmit,
  submitting,
}: JobFormSheetProps) {
  const t = useTranslations("automation");
  const tCommon = useTranslations("common");

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{editingJob ? t("editJob") : t("newJob")}</SheetTitle>
        </SheetHeader>
        <div className="flex-1 space-y-4 px-4">
          <div className="space-y-2">
            <Label htmlFor="job-name">{t("fields.name")}</Label>
            <Input
              id="job-name"
              value={form.name}
              onChange={(event) => onFormChange((prev) => ({ ...prev, name: event.target.value }))}
              placeholder={t("namePlaceholder")}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label>{t("fields.triggerType")}</Label>
              <Select
                value={form.trigger_type}
                onValueChange={(value) =>
                  onFormChange((prev) => ({
                    ...prev,
                    trigger_type: value as CreateScheduledJobParams["trigger_type"],
                  }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="interval">{t("triggerTypeInterval")}</SelectItem>
                  <SelectItem value="cron">{t("triggerTypeCronOption")}</SelectItem>
                  <SelectItem value="webhook">{t("triggerTypeWebhookOption")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>{t(TRIGGER_LABEL[form.trigger_type ?? "interval"])}</Label>
              <Input
                value={form.trigger_spec}
                onChange={(event) =>
                  onFormChange((prev) => ({ ...prev, trigger_spec: event.target.value }))
                }
                placeholder={form.trigger_type === "cron" ? "0 9 * * *" : "3600"}
                disabled={form.trigger_type === "webhook"}
              />
              {form.trigger_type === "cron" && (
                <p className="text-muted-foreground text-xs">{t("cronHelp")}</p>
              )}
              {form.trigger_type === "interval" && (
                <p className="text-muted-foreground text-xs">{t("triggerTypeInterval")}</p>
              )}
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="job-prompt">{t("fields.prompt")}</Label>
            <Textarea
              id="job-prompt"
              rows={4}
              value={form.prompt_template}
              onChange={(event) =>
                onFormChange((prev) => ({ ...prev, prompt_template: event.target.value }))
              }
              placeholder={t("promptPlaceholder")}
            />
            <p className="text-muted-foreground text-xs">{t("promptHelp")}</p>
          </div>
          <div className="space-y-2">
            <Label>{t("contextLabel")}</Label>
            <ContextSelector
              value={{
                codebaseId: form.codebase_id ?? undefined,
                knowledgeBaseId: form.knowledge_base_id ?? undefined,
              }}
              onChange={(ctx) =>
                onFormChange((prev) => ({
                  ...prev,
                  codebase_id: ctx.codebaseId ?? null,
                  knowledge_base_id: ctx.knowledgeBaseId ?? null,
                }))
              }
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label>{t("fields.modelId")}</Label>
              <SessionModelPicker
                value={form.model_id ?? undefined}
                onChange={(id) => onFormChange((prev) => ({ ...prev, model_id: id ?? null }))}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("fields.skillId")}</Label>
              <SessionSkillPicker
                value={form.skill_id ?? undefined}
                onChange={(id) => onFormChange((prev) => ({ ...prev, skill_id: id ?? null }))}
              />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="notify-server">{t("fields.notifyServer")}</Label>
              <Input
                id="notify-server"
                value={form.notify_channels?.[0]?.server_name ?? ""}
                onChange={(event) =>
                  onFormChange((prev) => ({
                    ...prev,
                    notify_channels: event.target.value
                      ? [
                          {
                            type: "mcp",
                            server_name: event.target.value,
                            channel_arg: prev.notify_channels?.[0]?.channel_arg ?? "",
                          },
                        ]
                      : [],
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="notify-channel">{t("fields.notifyChannel")}</Label>
              <Input
                id="notify-channel"
                value={form.notify_channels?.[0]?.channel_arg ?? ""}
                onChange={(event) =>
                  onFormChange((prev) => ({
                    ...prev,
                    notify_channels: prev.notify_channels?.[0]?.server_name
                      ? [{ ...prev.notify_channels[0], channel_arg: event.target.value }]
                      : [],
                  }))
                }
              />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-2">
              <Label>{t("fields.operatorScope")}</Label>
              <Select
                value={form.operator_scope ?? "none"}
                onValueChange={(value) =>
                  onFormChange((prev) => ({
                    ...prev,
                    operator_scope:
                      value === "none" ? null : (value as "owned" | "third_party_saas"),
                  }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">—</SelectItem>
                  <SelectItem value="owned">{t("scopeOwned")}</SelectItem>
                  <SelectItem value="third_party_saas">{t("scopeThirdPartySaas")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="operator-domains">{t("fields.operatorDomains")}</Label>
              <Input
                id="operator-domains"
                value={(form.operator_domains ?? []).join(", ")}
                onChange={(event) =>
                  onFormChange((prev) => ({
                    ...prev,
                    operator_domains: event.target.value
                      .split(/[,\n]+/)
                      .map((s) => s.trim())
                      .filter(Boolean),
                  }))
                }
                placeholder="ops-console"
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label>{t("fields.gateProfile")}</Label>
            <Select
              value={form.gate_profile ?? "standard"}
              onValueChange={(value) =>
                onFormChange((prev) => ({
                  ...prev,
                  gate_profile: value as CreateScheduledJobParams["gate_profile"],
                }))
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="loose">{t("gateLoose")}</SelectItem>
                <SelectItem value="standard">{t("gateStandard")}</SelectItem>
                <SelectItem value="strict">{t("gateStrict")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <Switch
              checked={form.enabled ?? true}
              onCheckedChange={(checked) => onFormChange((prev) => ({ ...prev, enabled: checked }))}
            />
            <Label>{t("fields.enabled")}</Label>
          </div>
        </div>
        <SheetFooter className="flex-row justify-end">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button onClick={onSubmit} disabled={submitting}>
            {submitting ? t("creating") : editingJob ? t("saveJob") : t("create")}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
