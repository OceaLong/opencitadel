"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
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
};

export function SkillsSettings({ embedded = false }: Props) {
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
      toast.error(e instanceof Error ? e.message : "加载失败");
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
        toast.success("Skill 已更新");
      } else {
        await skillsApi.create(payload);
        toast.success("Skill 已创建");
      }
      setDialogOpen(false);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await skillsApi.delete(id);
      toast.success("已删除");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
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
            Skill 模板
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            创建技能模板，会话级可选择开启（默认不启用）
          </p>
        </div>
        <Button size={embedded ? "xs" : "default"} onClick={openCreate}>
          <Plus className="mr-1 size-4" />
          新建 Skill
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
                      {s.is_builtin && <Badge variant="outline">内置</Badge>}
                    </CardTitle>
                    <CardDescription>{s.description || s.category}</CardDescription>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    {!s.is_builtin && (
                      <Button variant="ghost" size="icon" onClick={() => handleDelete(s.id)}>
                        <Trash2 className="text-destructive size-4" />
                      </Button>
                    )}
                    <Button variant="outline" size="sm" onClick={() => openEdit(s)}>
                      编辑
                    </Button>
                  </div>
                </div>
              </CardHeader>
            </Card>
          ))}
          {skills.length === 0 && (
            <p className="text-muted-foreground py-8 text-center text-sm">暂无 Skill</p>
          )}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto shadow-[var(--shadow-panel)]">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑 Skill" : "新建 Skill"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="grid grid-cols-3 gap-4">
              <div className="col-span-2 space-y-2">
                <Label>名称</Label>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>图标</Label>
                <Input
                  value={form.icon}
                  onChange={(e) => setForm({ ...form, icon: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>Slug</Label>
              <Input
                value={form.slug}
                onChange={(e) => setForm({ ...form, slug: e.target.value })}
                disabled={editing?.is_builtin}
              />
            </div>
            <div className="space-y-2">
              <Label>描述</Label>
              <Input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>System Prompt</Label>
              <Textarea
                rows={5}
                value={form.system_prompt}
                onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>工具白名单（逗号分隔，空=不过滤）</Label>
              <Input
                value={toolsText}
                onChange={(e) => setToolsText(e.target.value)}
                placeholder="read_file, shell_execute, search_web, mcp_jina_*"
              />
              <p className="text-muted-foreground text-xs">
                支持通配符，例如 <code>mcp_*</code>（全部 MCP）、<code>mcp_jina_*</code>（指定 MCP 服务）
              </p>
            </div>
            <div className="border-border/70 bg-muted/20 space-y-3 rounded-xl border p-3">
              <Label>动态工具组</Label>
              <div className="flex flex-wrap gap-4">
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={allowMcpTools} onCheckedChange={setAllowMcpTools} />
                  允许所有 MCP 工具 (mcp_*)
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={allowA2aTools} onCheckedChange={setAllowA2aTools} />
                  允许 A2A 远程 Agent (a2a)
                </label>
              </div>
            </div>
            <div className="space-y-2">
              <Label>绑定的 MCP 服务（空=使用全部已启用服务）</Label>
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
                  <span className="text-muted-foreground text-sm">暂无 MCP 服务</span>
                )}
              </div>
            </div>
            <div className="space-y-2">
              <Label>绑定的 A2A 服务（空=使用全部已启用服务）</Label>
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
                  <span className="text-muted-foreground text-sm">暂无 A2A 服务</span>
                )}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>最大迭代次数</Label>
                <Input
                  type="number"
                  min={1}
                  placeholder="留空=默认"
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
                <Label>最大重试次数</Label>
                <Input
                  type="number"
                  min={0}
                  placeholder="留空=默认"
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
                <Label>最大搜索结果数</Label>
                <Input
                  type="number"
                  min={1}
                  placeholder="留空=默认"
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
                <Label>温度覆盖 (0-2)</Label>
                <Input
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  placeholder="留空=模型默认"
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
              <Label>推荐模型</Label>
              <Select
                value={form.recommended_model_id || "none"}
                onValueChange={(v) =>
                  setForm({ ...form, recommended_model_id: v === "none" ? undefined : v })
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="无" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">无</SelectItem>
                  {models.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>触发示例（每行一条）</Label>
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
              <Label>全局启用</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              取消
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="mr-1 size-4 animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
