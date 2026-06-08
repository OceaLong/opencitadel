"use client";

import { useEffect, useState } from "react";
import {
  Brain,
  Cpu,
  LayoutGrid,
  LayoutList,
  Loader2,
  Settings,
  Sparkles,
  Trash,
  Wrench,
} from "lucide-react";
import { toast } from "sonner";

import { MemorySettings } from "@/components/settings/memory-settings";
import { ModelsSettings } from "@/components/settings/models-settings";
import { SkillsSettings } from "@/components/settings/skills-settings";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { type SettingTab, useManusSettings } from "@/hooks/use-manus-settings";
import type { AgentConfig, ListA2AServerItem, ListMCPServerItem } from "@/lib/api";

// ==================== 通用配置 ====================

type CommonSettingProps = {
  config: AgentConfig;
  onChange: (config: AgentConfig) => void;
};

function CommonSetting({ config, onChange }: CommonSettingProps) {
  const handleChange = (field: keyof AgentConfig, value: string) => {
    const numValue = value === "" ? undefined : Number(value);
    onChange({ ...config, [field]: numValue });
  };

  return (
    <form className="w-full px-1" onSubmit={(e) => e.preventDefault()}>
      <FieldGroup>
        <FieldSet>
          <FieldLegend className="text-foreground text-lg font-semibold">通用配置</FieldLegend>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="max_iterations">最大计划迭代次数</FieldLabel>
              <Input
                id="max_iterations"
                type="number"
                placeholder="Agent最大迭代次数"
                value={config.max_iterations ?? 100}
                onChange={(e) => handleChange("max_iterations", e.target.value)}
                min={0}
                max={200}
              />
              <FieldDescription className="text-xs">
                执行Agent最大能迭代循环调用工具的次数，默认为100
              </FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="max_retries">最大重试次数</FieldLabel>
              <Input
                id="max_retries"
                type="number"
                placeholder="LLM/Tool最大重试次数"
                value={config.max_retries ?? 3}
                onChange={(e) => handleChange("max_retries", e.target.value)}
                min={0}
                max={10}
              />
              <FieldDescription className="text-xs">默认情况下，最大重试次数为3</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="max_search_results">最大搜索结果</FieldLabel>
              <Input
                id="max_search_results"
                type="number"
                placeholder="搜索工具返回的最大结果数"
                value={config.max_search_results ?? 10}
                onChange={(e) => handleChange("max_search_results", e.target.value)}
                min={0}
                max={30}
              />
              <FieldDescription className="text-xs">
                默认情况下，每个搜索步骤包含 10 个结果。
              </FieldDescription>
            </Field>
          </FieldGroup>
        </FieldSet>
      </FieldGroup>
    </form>
  );
}

// ==================== A2A Agent 配置 ====================

type A2ASettingProps = {
  servers: ListA2AServerItem[];
  loading: boolean;
  onToggleEnabled: (id: string, enabled: boolean) => void;
  onDelete: (id: string) => void;
  onAdd: (baseUrl: string) => Promise<boolean>;
};

function A2ASetting({ servers, loading, onToggleEnabled, onDelete, onAdd }: A2ASettingProps) {
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [addUrl, setAddUrl] = useState("");
  const [adding, setAdding] = useState(false);

  const handleAdd = async () => {
    if (!addUrl.trim()) {
      toast.error("请输入远程 Agent 地址");
      return;
    }
    setAdding(true);
    try {
      const success = await onAdd(addUrl.trim());
      if (success) {
        setAddUrl("");
        setAddDialogOpen(false);
      }
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="w-full px-1">
      <FieldGroup>
        <FieldSet>
          <FieldLegend className="text-foreground flex w-full items-center justify-between text-lg font-semibold">
            A2A Agent 配置
            <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
              <DialogTrigger asChild>
                <Button type="button" size="xs" className="cursor-pointer">
                  添加远程Agent
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle className="text-foreground">添加远程Agent</DialogTitle>
                  <DialogDescription className="text-muted-foreground">
                    MyManus 使用标准的 A2A 协议来连接远程 Agent。
                    <br />
                    请将您的配置粘贴到下方，然后点击&ldquo;添加&rdquo;即可添加 Agent。
                  </DialogDescription>
                </DialogHeader>
                <form
                  className="w-full"
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleAdd();
                  }}
                >
                  <FieldGroup>
                    <FieldSet>
                      <Field>
                        <Input
                          id="a2a_base_url"
                          type="url"
                          placeholder="Example: https://my-manus.com/weather-agent"
                          value={addUrl}
                          onChange={(e) => setAddUrl(e.target.value)}
                          disabled={adding}
                        />
                      </Field>
                    </FieldSet>
                  </FieldGroup>
                </form>
                <DialogFooter>
                  <DialogClose asChild>
                    <Button variant="outline" className="cursor-pointer" disabled={adding}>
                      取消
                    </Button>
                  </DialogClose>
                  <Button className="cursor-pointer" onClick={handleAdd} disabled={adding}>
                    {adding && <Loader2 className="animate-spin" />}
                    添加
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </FieldLegend>
          <FieldDescription className="text-sm">
            模型上下文协议 (MCP) 通过集成外部工具来增强 MyManus
            的性能，例如私有域搜索、网页浏览、订餐、PPT 生成等任务。
          </FieldDescription>

          {/* 加载态 */}
          {loading && (
            <div className="flex justify-center py-8">
              <Loader2 className="text-muted-foreground size-6 animate-spin" />
            </div>
          )}

          {/* 空态 */}
          {!loading && servers.length === 0 && (
            <div className="text-muted-foreground py-8 text-center text-sm">
              暂无 A2A Agent，请点击上方按钮添加
            </div>
          )}

          {/* 列表 */}
          {!loading && servers.length > 0 && (
            <ItemGroup className="gap-3">
              {servers.map((server) => (
                <Item key={server.id} variant="outline">
                  <ItemContent>
                    <ItemTitle className="text-md text-foreground flex w-full items-center justify-between font-semibold">
                      <div className="flex items-center gap-2">
                        {server.name}
                        {!server.enabled && <Badge>禁用</Badge>}
                      </div>
                      <div className="flex items-center justify-center gap-2">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          className="cursor-pointer"
                          onClick={() => onDelete(server.id)}
                        >
                          <Trash />
                        </Button>
                        <Switch
                          checked={server.enabled}
                          onCheckedChange={(checked) => onToggleEnabled(server.id, checked)}
                        />
                      </div>
                    </ItemTitle>
                    {server.description && <ItemDescription>{server.description}</ItemDescription>}
                    <ItemDescription className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <LayoutList size={12} />
                      {server.input_modes?.map((mode) => (
                        <Badge
                          key={`in-${mode}`}
                          variant="secondary"
                          className="text-muted-foreground"
                        >
                          输入: {mode}
                        </Badge>
                      ))}
                      {server.output_modes?.map((mode) => (
                        <Badge
                          key={`out-${mode}`}
                          variant="secondary"
                          className="text-muted-foreground"
                        >
                          输出: {mode}
                        </Badge>
                      ))}
                      <Badge
                        variant={server.streaming ? "secondary" : "outline"}
                        className={
                          server.streaming ? "text-muted-foreground" : "text-muted-foreground/70"
                        }
                      >
                        流式输出: {server.streaming ? "开启" : "关闭"}
                      </Badge>
                      <Badge
                        variant={server.push_notifications ? "secondary" : "outline"}
                        className={
                          server.push_notifications
                            ? "text-muted-foreground"
                            : "text-muted-foreground/70"
                        }
                      >
                        推送通知: {server.push_notifications ? "开启" : "关闭"}
                      </Badge>
                    </ItemDescription>
                  </ItemContent>
                </Item>
              ))}
            </ItemGroup>
          )}
        </FieldSet>
      </FieldGroup>
    </div>
  );
}

// ==================== MCP 服务器 ====================

type MCPSettingProps = {
  servers: ListMCPServerItem[];
  loading: boolean;
  onToggleEnabled: (serverName: string, enabled: boolean) => void;
  onDelete: (serverName: string) => void;
  onAdd: (config: string) => Promise<boolean>;
};

function MCPSetting({ servers, loading, onToggleEnabled, onDelete, onAdd }: MCPSettingProps) {
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [addConfig, setAddConfig] = useState("");
  const [adding, setAdding] = useState(false);

  const mcpConfigPlaceholder = `{
  "mcpServers": {
    "qiniu": {
      "command": "uvx",
      "args": [
        "qiniu-mcp-server"
      ],
      "env": {
        "QINIU_ACCESS_KEY": "YOUR_ACCESS_KEY",
        "QINIU_SECRET_KEY": "YOUR_SECRET_KEY"
      }
    }
  }
}`;

  const handleAdd = async () => {
    if (!addConfig.trim()) {
      toast.error("请输入 MCP 服务器配置");
      return;
    }
    setAdding(true);
    try {
      const success = await onAdd(addConfig.trim());
      if (success) {
        setAddConfig("");
        setAddDialogOpen(false);
      }
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="w-full px-1">
      <FieldGroup>
        <FieldSet>
          <FieldLegend className="text-foreground flex w-full items-center justify-between text-lg font-semibold">
            MCP 服务器
            <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
              <DialogTrigger asChild>
                <Button type="button" size="xs" className="cursor-pointer">
                  添加服务器
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle className="text-foreground">添加新的 MCP 服务器</DialogTitle>
                  <DialogDescription className="text-muted-foreground">
                    MyManus 使用标准的 JSON MCP 配置来创建新服务器。
                    请将您的配置粘贴到下方，然后点击&ldquo;添加&rdquo;即可添加新服务器。
                  </DialogDescription>
                </DialogHeader>
                <form
                  className="w-full"
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleAdd();
                  }}
                >
                  <FieldGroup>
                    <FieldSet>
                      <Field>
                        <Textarea
                          id="mcp_config"
                          placeholder={mcpConfigPlaceholder}
                          value={addConfig}
                          onChange={(e) => setAddConfig(e.target.value)}
                          className="min-h-[200px] font-mono text-xs"
                          disabled={adding}
                        />
                      </Field>
                    </FieldSet>
                  </FieldGroup>
                </form>
                <DialogFooter>
                  <DialogClose asChild>
                    <Button variant="outline" className="cursor-pointer" disabled={adding}>
                      取消
                    </Button>
                  </DialogClose>
                  <Button className="cursor-pointer" onClick={handleAdd} disabled={adding}>
                    {adding && <Loader2 className="animate-spin" />}
                    添加
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </FieldLegend>
          <FieldDescription className="text-sm">
            模型上下文协议 (MCP) 通过集成外部工具来增强 MyManus
            的性能，例如私有域搜索、网页浏览、订餐、PPT 生成等任务。
          </FieldDescription>

          {/* 加载态 */}
          {loading && (
            <div className="flex justify-center py-8">
              <Loader2 className="text-muted-foreground size-6 animate-spin" />
            </div>
          )}

          {/* 空态 */}
          {!loading && servers.length === 0 && (
            <div className="text-muted-foreground py-8 text-center text-sm">
              暂无 MCP 服务器，请点击上方按钮添加
            </div>
          )}

          {/* 列表 */}
          {!loading && servers.length > 0 && (
            <ItemGroup className="gap-3">
              {servers.map((server) => (
                <Item key={server.server_name} variant="outline">
                  <ItemContent>
                    <ItemTitle className="text-md text-foreground flex w-full items-center justify-between font-semibold">
                      <div className="flex items-center gap-2">
                        {server.server_name}
                        <Badge>{server.transport}</Badge>
                        {!server.enabled && <Badge>禁用</Badge>}
                      </div>
                      <div className="flex items-center justify-center gap-2">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          className="cursor-pointer"
                          onClick={() => onDelete(server.server_name)}
                        >
                          <Trash />
                        </Button>
                        <Switch
                          checked={server.enabled}
                          onCheckedChange={(checked) =>
                            onToggleEnabled(server.server_name, checked)
                          }
                        />
                      </div>
                    </ItemTitle>
                    {server.tools.length > 0 && (
                      <ItemDescription className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <Wrench size={12} />
                        {server.tools.map((tool) => (
                          <Badge key={tool} variant="secondary" className="text-muted-foreground">
                            {tool}
                          </Badge>
                        ))}
                      </ItemDescription>
                    )}
                  </ItemContent>
                </Item>
              ))}
            </ItemGroup>
          )}
        </FieldSet>
      </FieldGroup>
    </div>
  );
}

// ==================== 设置弹窗主组件 ====================

const SETTING_MENUS: Array<{
  key: SettingTab;
  icon: typeof Settings;
  title: string;
}> = [
  { key: "common-setting", icon: Settings, title: "通用配置" },
  { key: "models-setting", icon: Cpu, title: "模型管理" },
  { key: "skills-setting", icon: Sparkles, title: "Skill 模板" },
  { key: "memory-setting", icon: Brain, title: "长期记忆" },
  { key: "a2a-setting", icon: LayoutGrid, title: "A2A Agent 配置" },
  { key: "mcp-setting", icon: Wrench, title: "MCP 服务器" },
];

export function ManusSettings() {
  // ---- 防止 SSR hydration 不匹配（Radix Dialog 在服务端/客户端生成不同的 aria-controls ID）----
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setMounted(true), 0);
    return () => window.clearTimeout(id);
  }, []);

  // ---- 弹窗 & 导航 ----
  const [open, setOpen] = useState(false);
  const [activeSetting, setActiveSetting] = useState<SettingTab>("common-setting");

  const showFooterSave = activeSetting === "common-setting";
  const {
    agentConfig,
    setAgentConfig,
    mcpServers,
    a2aServers,
    loadingConfig,
    loadingMCP,
    loadingA2A,
    saving,
    handleSave,
    handleMCPToggle,
    handleMCPDelete,
    handleMCPAdd,
    handleA2AToggle,
    handleA2ADelete,
    handleA2AAdd,
  } = useManusSettings(open, activeSetting);

  // 客户端挂载前，仅渲染普通按钮占位，避免 Radix Dialog SSR hydration 不匹配
  if (!mounted) {
    return (
      <Button variant="outline" size="icon-sm" className="cursor-pointer">
        <Settings />
      </Button>
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {/* 触发按钮 */}
      <DialogTrigger asChild>
        <Button variant="outline" size="icon-sm" className="cursor-pointer">
          <Settings />
        </Button>
      </DialogTrigger>

      {/* 弹窗内容 */}
      <DialogContent className="!max-w-[920px] shadow-[var(--shadow-panel)]">
        {/* 头部 */}
        <DialogHeader className="border-border/70 border-b pb-4">
          <DialogTitle className="text-foreground">MyManus 设置</DialogTitle>
          <DialogDescription className="text-muted-foreground">
            在此管理您的 MyManus 设置。
          </DialogDescription>
        </DialogHeader>

        {/* 中间主体 */}
        <div className="flex flex-row gap-4">
          {/* 左侧导航菜单 */}
          <div className="w-[168px] shrink-0">
            <div className="flex flex-col gap-0">
              {SETTING_MENUS.map((menu) => (
                <Button
                  key={menu.key}
                  variant={activeSetting === menu.key ? "default" : "ghost"}
                  className="cursor-pointer justify-start px-2 text-sm"
                  onClick={() => setActiveSetting(menu.key)}
                >
                  <menu.icon className="size-4" />
                  <span className="truncate">{menu.title}</span>
                </Button>
              ))}
            </div>
          </div>

          {/* 分隔符 */}
          <Separator orientation="vertical" />

          {/* 右侧内容 */}
          <div className="scrollbar-hide h-[500px] flex-1 overflow-y-auto">
            {loadingConfig && activeSetting === "common-setting" ? (
              <div className="flex h-full items-center justify-center">
                <Loader2 className="text-muted-foreground size-6 animate-spin" />
              </div>
            ) : (
              <>
                {activeSetting === "common-setting" && (
                  <CommonSetting config={agentConfig} onChange={setAgentConfig} />
                )}
              </>
            )}
            {activeSetting === "models-setting" && <ModelsSettings embedded />}
            {activeSetting === "skills-setting" && <SkillsSettings embedded />}
            {activeSetting === "memory-setting" && <MemorySettings embedded />}
            {activeSetting === "a2a-setting" && (
              <A2ASetting
                servers={a2aServers}
                loading={loadingA2A}
                onToggleEnabled={handleA2AToggle}
                onDelete={handleA2ADelete}
                onAdd={handleA2AAdd}
              />
            )}
            {activeSetting === "mcp-setting" && (
              <MCPSetting
                servers={mcpServers}
                loading={loadingMCP}
                onToggleEnabled={handleMCPToggle}
                onDelete={handleMCPDelete}
                onAdd={handleMCPAdd}
              />
            )}
          </div>
        </div>

        {showFooterSave && (
          <DialogFooter className="border-t pt-4">
            <DialogClose asChild>
              <Button variant="outline" className="cursor-pointer">
                取消
              </Button>
            </DialogClose>
            <Button className="cursor-pointer" disabled={saving} onClick={handleSave}>
              {saving && <Loader2 className="animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
