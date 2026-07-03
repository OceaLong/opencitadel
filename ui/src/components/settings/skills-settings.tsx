"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { configApi } from "@/lib/api/config";
import { modelsApi } from "@/lib/api/models";
import { skillsApi } from "@/lib/api/skills";
import type {
  CreateSkillParams,
  ListA2AServerItem,
  ListMCPServerItem,
  LLMModel,
  Skill,
} from "@/lib/api/types";
import { cn } from "@/lib/utils";

const defaultAgentParams = {
  max_iterations: undefined as number | undefined,
  max_retries: undefined as number | undefined,
  max_search_results: undefined as number | undefined,
  temperature_override: undefined as number | undefined,
};

const emptyForm: CreateSkillParams = {
  name: "",
  slug: "",
  description: "",
  icon: "🤖",
  category: "general",
  system_prompt: "",
  allowed_tools: [],
  agent_params: { ...defaultAgentParams },
  examples: [],
  enabled: true,
};

type Props = {
  embedded?: boolean;
  isAdmin?: boolean;
  userId?: string;
};

function canManageSkill(
  skill: { visibility?: string; owner_user_id?: string | null; is_builtin: boolean },
  isAdmin: boolean,
  userId?: string,
) {
  if (isAdmin) return !skill.is_builtin;
  if (skill.is_builtin || skill.visibility === "global") return false;
  return skill.owner_user_id === userId;
}

export function SkillsSettings({ embedded = false, isAdmin = false, userId }: Props) {
  const tNav = useTranslations("settingsNav");
  const t = useTranslations("settingsSkills");
  const tCommon = useTranslations("common");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [models, setModels] = useState<LLMModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Skill | null>(null);
  const [form, setForm] = useState<CreateSkillParams>(emptyForm);
  const [toolsText, setToolsText] = useState("");
  const [examplesText, setExamplesText] = useState("");
  const [allowMcpTools, setAllowMcpTools] = useState(false);
  const [allowA2aTools, setAllowA2aTools] = useState(false);
  const [mcpServers, setMcpServers] = useState<ListMCPServerItem[]>([]);
  const [a2aServers, setA2aServers] = useState<ListA2AServerItem[]>([]);
  const [selectedMcpRefs, setSelectedMcpRefs] = useState<string[]>([]);
  const [selectedA2aRefs, setSelectedA2aRefs] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [skillsData, modelsData] = await Promise.all([skillsApi.list(), modelsApi.list()]);
      setSkills(skillsData.skills);
      setModels(modelsData.models);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tCommon("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const loadServerOptions = useCallback(async () => {
    try {
      const [mcpData, a2aData] = await Promise.all([
        configApi.getMCPServers(),
        configApi.getA2AServers(),
      ]);
      setMcpServers(mcpData.mcp_servers);
      setA2aServers(a2aData.a2a_servers);
    } catch {
      setMcpServers([]);
      setA2aServers([]);
    }
  }, []);

  const resetToolGroupState = (allowedTools: string[] = []) => {
    setAllowMcpTools(allowedTools.includes("mcp_*"));
    setAllowA2aTools(allowedTools.includes("a2a"));
    const manualTools = allowedTools.filter((tool) => tool !== "mcp_*" && tool !== "a2a");
    setToolsText(manualTools.join(", "));
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ ...emptyForm, agent_params: { ...defaultAgentParams } });
    resetToolGroupState();
    setSelectedMcpRefs([]);
    setSelectedA2aRefs([]);
    setExamplesText("");
    void loadServerOptions();
    setDialogOpen(true);
  };

  const openEdit = (s: Skill) => {
    setEditing(s);
    setForm({
      name: s.name,
      slug: s.slug,
      description: s.description,
      icon: s.icon,
      category: s.category,
      system_prompt: s.system_prompt,
      allowed_tools: s.allowed_tools,
      mcp_server_refs: s.mcp_server_refs ?? [],
      a2a_server_refs: s.a2a_server_refs ?? [],
      recommended_model_id: s.recommended_model_id,
      agent_params: {
        max_iterations: s.agent_params?.max_iterations,
        max_retries: s.agent_params?.max_retries,
        max_search_results: s.agent_params?.max_search_results,
        temperature_override: s.agent_params?.temperature_override,
      },
      examples: s.examples,
      enabled: s.enabled,
    });
    resetToolGroupState(s.allowed_tools);
    setSelectedMcpRefs(s.mcp_server_refs ?? []);
    setSelectedA2aRefs(s.a2a_server_refs ?? []);
    setExamplesText(s.examples.join("\n"));
    void loadServerOptions();
    setDialogOpen(true);
  };

  const toggleMcpRef = (serverName: string) => {
    setSelectedMcpRefs((prev) =>
      prev.includes(serverName) ? prev.filter((item) => item !== serverName) : [...prev, serverName],
    );
  };

  const toggleA2aRef = (serverId: string) => {
    setSelectedA2aRefs((prev) =>
      prev.includes(serverId) ? prev.filter((item) => item !== serverId) : [...prev, serverId],
    );
  };

  const parseAgentParams = () => {
    const p = form.agent_params || {};
    const result: CreateSkillParams["agent_params"] = {};
    if (p.max_iterations != null && p.max_iterations !== ("" as unknown as number)) {
      result.max_iterations = Number(p.max_iterations);
    }
    if (p.max_retries != null && p.max_retries !== ("" as unknown as number)) {
      result.max_retries = Number(p.max_retries);
    }
    if (p.max_search_results != null && p.max_search_results !== ("" as unknown as number)) {
      result.max_search_results = Number(p.max_search_results);
    }
    if (p.temperature_override != null && p.temperature_override !== ("" as unknown as number)) {
      result.temperature_override = Number(p.temperature_override);
    }
    return Object.keys(result).length > 0 ? result : {};
  };

  const handleSave = async () => {
    setSaving(true);
    const manualTools = toolsText
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const allowedTools = [
      ...manualTools,
      ...(allowMcpTools ? ["mcp_*"] : []),
      ...(allowA2aTools ? ["a2a"] : []),
    ];
    const payload = {
      ...form,
      allowed_tools: allowedTools,
      mcp_server_refs: selectedMcpRefs,
      a2a_server_refs: selectedA2aRefs,
      examples: examplesText
        .split("\n")
        .map((t) => t.trim())
        .filter(Boolean),
      agent_params: parseAgentParams(),
    };
    try {
      if (editing) {
        await skillsApi.update(editing.id, payload);
        toast.success(t("skillUpdated"));
      } else {
        await skillsApi.create(payload);
        toast.success(t("skillCreated"));
      }
      setDialogOpen(false);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tCommon("saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await skillsApi.delete(id);
      toast.success(tCommon("deleted"));
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tCommon("deleteFailed"));
    }
  };

  return (
    <div className={embedded ? "w-full px-1" : "max-w-5xl"}>
      <div className={`flex items-center justify-between ${embedded ? "mb-4" : "mb-6"}`}>
        <div>
          <h2
            className={
              embedded
                ? "text-foreground text-lg font-semibold"
                : "text-2xl font-semibold tracking-tight"
            }
          >
            {tNav("skills")}
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">{t("description")}</p>
        </div>
        <Button size={embedded ? "xs" : "default"} onClick={openCreate}>
          <Plus className="mr-1 size-4" />
          {t("createSkill")}
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="size-6 animate-spin" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3">
          {skills.map((s) => (
            <Card
              key={s.id}
              className={cn(
                "hover:border-border transition-all hover:shadow-[var(--shadow-card-hover)]",
                !s.enabled && "opacity-60",
              )}
            >
              <CardHeader className="pb-2">
                <div className="flex justify-between gap-2">
                  <div className="min-w-0">
                    <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                      <span>{s.icon}</span>
                      {s.name}
                      {s.is_builtin && <Badge variant="outline">{tCommon("builtin")}</Badge>}
                    </CardTitle>
                    <CardDescription>{s.description || s.category}</CardDescription>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    {canManageSkill(s, isAdmin, userId) ? (
                      <>
                    <Button variant="ghost" size="icon" onClick={() => handleDelete(s.id)}>
                      <Trash2 className="text-destructive size-4" />
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => openEdit(s)}>
                      {tCommon("edit")}
                    </Button>
                      </>
                    ) : null}
                  </div>
                </div>
              </CardHeader>
            </Card>
          ))}
          {skills.length === 0 && (
            <p className="text-muted-foreground py-8 text-center text-sm">{t("noSkills")}</p>
          )}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto shadow-[var(--shadow-panel)]">
          <DialogHeader>
            <DialogTitle>{editing ? t("editSkill") : t("createSkill")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="grid grid-cols-3 gap-4">
              <div className="col-span-2 space-y-2">
                <Label>{t("name")}</Label>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t("icon")}</Label>
                <Input
                  value={form.icon}
                  onChange={(e) => setForm({ ...form, icon: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("slug")}</Label>
              <Input
                value={form.slug}
                onChange={(e) => setForm({ ...form, slug: e.target.value })}
                disabled={editing?.is_builtin}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("descriptionLabel")}</Label>
              <Input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("systemPrompt")}</Label>
              <Textarea
                rows={5}
                value={form.system_prompt}
                onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("toolWhitelist")}</Label>
              <Input
                value={toolsText}
                onChange={(e) => setToolsText(e.target.value)}
                placeholder="read_file, shell_execute, search_web, mcp_jina_*"
              />
              <p className="text-muted-foreground text-xs">{t("toolWhitelistHint")}</p>
            </div>
            <div className="border-border/70 bg-muted/20 space-y-3 rounded-xl border p-3">
              <Label>{t("dynamicToolGroups")}</Label>
              <div className="flex flex-wrap gap-4">
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={allowMcpTools} onCheckedChange={setAllowMcpTools} />
                  {t("allowAllMcp")}
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={allowA2aTools} onCheckedChange={setAllowA2aTools} />
                  {t("allowA2a")}
                </label>
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("boundMcpServers")}</Label>
              <div className="flex flex-wrap gap-2">
                {mcpServers.map((server) => (
                  <Button
                    key={server.server_name}
                    type="button"
                    size="sm"
                    variant={selectedMcpRefs.includes(server.server_name) ? "default" : "outline"}
                    onClick={() => toggleMcpRef(server.server_name)}
                  >
                    {server.server_name}
                  </Button>
                ))}
                {mcpServers.length === 0 && (
                  <span className="text-muted-foreground text-sm">{t("noMcpServices")}</span>
                )}
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("boundA2aServers")}</Label>
              <div className="flex flex-wrap gap-2">
                {a2aServers.map((server) => (
                  <Button
                    key={server.id}
                    type="button"
                    size="sm"
                    variant={selectedA2aRefs.includes(server.id) ? "default" : "outline"}
                    onClick={() => toggleA2aRef(server.id)}
                  >
                    {server.name || server.id}
                  </Button>
                ))}
                {a2aServers.length === 0 && (
                  <span className="text-muted-foreground text-sm">{t("noA2aServices")}</span>
                )}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t("maxIterations")}</Label>
                <Input
                  type="number"
                  min={1}
                  placeholder={t("leaveBlankDefault")}
                  value={form.agent_params?.max_iterations ?? ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      agent_params: {
                        ...form.agent_params,
                        max_iterations: e.target.value ? Number(e.target.value) : undefined,
                      },
                    })
                  }
                />
              </div>
              <div className="space-y-2">
                <Label>{t("maxRetries")}</Label>
                <Input
                  type="number"
                  min={0}
                  placeholder={t("leaveBlankDefault")}
                  value={form.agent_params?.max_retries ?? ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      agent_params: {
                        ...form.agent_params,
                        max_retries: e.target.value ? Number(e.target.value) : undefined,
                      },
                    })
                  }
                />
              </div>
              <div className="space-y-2">
                <Label>{t("maxSearchResults")}</Label>
                <Input
                  type="number"
                  min={1}
                  placeholder={t("leaveBlankDefault")}
                  value={form.agent_params?.max_search_results ?? ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      agent_params: {
                        ...form.agent_params,
                        max_search_results: e.target.value ? Number(e.target.value) : undefined,
                      },
                    })
                  }
                />
              </div>
              <div className="space-y-2">
                <Label>{t("temperatureOverride")}</Label>
                <Input
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  placeholder={t("leaveBlankModelDefault")}
                  value={form.agent_params?.temperature_override ?? ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      agent_params: {
                        ...form.agent_params,
                        temperature_override: e.target.value ? Number(e.target.value) : undefined,
                      },
                    })
                  }
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("recommendedModel")}</Label>
              <Select
                value={form.recommended_model_id || "none"}
                onValueChange={(v) =>
                  setForm({ ...form, recommended_model_id: v === "none" ? undefined : v })
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder={tCommon("none")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{tCommon("none")}</SelectItem>
                  {models.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>{t("triggerExamples")}</Label>
              <Textarea
                rows={3}
                value={examplesText}
                onChange={(e) => setExamplesText(e.target.value)}
              />
            </div>
            <div className="border-border/70 bg-muted/20 flex items-center justify-between rounded-xl border p-3">
              <Switch
                checked={form.enabled}
                onCheckedChange={(v) => setForm({ ...form, enabled: v })}
              />
              <Label>{t("globallyEnabled")}</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              {tCommon("cancel")}
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="mr-1 size-4 animate-spin" />}
              {tCommon("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
