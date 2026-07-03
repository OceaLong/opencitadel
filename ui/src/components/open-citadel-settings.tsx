"use client";

import { useEffect, useState } from "react";
import {
  LayoutGrid,
  LayoutList,
  Loader2,
  Settings,
} from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  IconDelete,
  IconIntegration,
  IconMemory,
  IconModel,
  IconSkill,
  IconTool,
} from "@/lib/icons";

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

import { type SettingTab, useOpenCitadelSettings } from "@/hooks/use-open-citadel-settings";
import type { AgentConfig, ListA2AServerItem, ListMCPServerItem } from "@/lib/api";

// ==================== 通用配置 ====================

type CommonSettingProps = {
  config: AgentConfig;
  onChange: (config: AgentConfig) => void;
};

function CommonSetting({ config, onChange }: CommonSettingProps) {
  const t = useTranslations("settings");
  const handleChange = (field: keyof AgentConfig, value: string) => {
    const numValue = value === "" ? undefined : Number(value);
    onChange({ ...config, [field]: numValue });
  };

  return (
    <form className="w-full px-1" onSubmit={(e) => e.preventDefault()}>
      <FieldGroup>
        <FieldSet>
          <FieldLegend className="text-foreground text-lg font-semibold">{t("common")}</FieldLegend>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="max_iterations">{t("maxIterations")}</FieldLabel>
              <Input
                id="max_iterations"
                type="number"
                placeholder={t("maxIterationsPlaceholder")}
                value={config.max_iterations ?? 100}
                onChange={(e) => handleChange("max_iterations", e.target.value)}
                min={0}
                max={200}
              />
              <FieldDescription className="text-xs">{t("maxIterationsDesc")}</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="max_retries">{t("maxRetries")}</FieldLabel>
              <Input
                id="max_retries"
                type="number"
                placeholder={t("maxRetriesPlaceholder")}
                value={config.max_retries ?? 3}
                onChange={(e) => handleChange("max_retries", e.target.value)}
                min={0}
                max={10}
              />
              <FieldDescription className="text-xs">{t("maxRetriesDesc")}</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="max_search_results">{t("maxSearchResults")}</FieldLabel>
              <Input
                id="max_search_results"
                type="number"
                placeholder={t("maxSearchResultsPlaceholder")}
                value={config.max_search_results ?? 10}
                onChange={(e) => handleChange("max_search_results", e.target.value)}
                min={0}
                max={30}
              />
              <FieldDescription className="text-xs">{t("maxSearchResultsDesc")}</FieldDescription>
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

export function A2ASetting({ servers, loading, onToggleEnabled, onDelete, onAdd }: A2ASettingProps) {
  const t = useTranslations("settings");
  const tCommon = useTranslations("common");
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [addUrl, setAddUrl] = useState("");
  const [adding, setAdding] = useState(false);

  const handleAdd = async () => {
    if (!addUrl.trim()) {
      toast.error(t("enterAgentUrl"));
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
            {t("a2a")}
            <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
              <DialogTrigger asChild>
                <Button type="button" size="xs" className="cursor-pointer">
                  {t("addRemoteAgent")}
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle className="text-foreground">{t("addRemoteAgent")}</DialogTitle>
                  <DialogDescription className="text-muted-foreground">
                    {t("a2aAddDescription")}
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
                          placeholder="Example: https://opencitadel.example/weather-agent"
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
                      {tCommon("cancel")}
                    </Button>
                  </DialogClose>
                  <Button className="cursor-pointer" onClick={handleAdd} disabled={adding}>
                    {adding && <Loader2 className="animate-spin" />}
                    {tCommon("add")}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </FieldLegend>
          <FieldDescription className="text-sm">{t("a2aDescription")}</FieldDescription>

          {/* 加载态 */}
          {loading && (
            <div className="flex justify-center py-8">
              <Loader2 className="text-muted-foreground size-6 animate-spin" />
            </div>
          )}

          {/* 空态 */}
          {!loading && servers.length === 0 && (
            <div className="text-muted-foreground py-8 text-center text-sm">{t("noA2aAgents")}</div>
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
                        {!server.enabled && <Badge>{tCommon("disabled")}</Badge>}
                      </div>
                      <div className="flex items-center justify-center gap-2">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          className="cursor-pointer"
                          onClick={() => onDelete(server.id)}
                        >
                          <IconDelete />
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
                          {tCommon("input")}: {mode}
                        </Badge>
                      ))}
                      {server.output_modes?.map((mode) => (
                        <Badge
                          key={`out-${mode}`}
                          variant="secondary"
                          className="text-muted-foreground"
                        >
                          {tCommon("output")}: {mode}
                        </Badge>
                      ))}
                      <Badge
                        variant={server.streaming ? "secondary" : "outline"}
                        className={
                          server.streaming ? "text-muted-foreground" : "text-muted-foreground/70"
                        }
                      >
                        {tCommon("streaming")}: {server.streaming ? tCommon("on") : tCommon("off")}
                      </Badge>
                      <Badge
                        variant={server.push_notifications ? "secondary" : "outline"}
                        className={
                          server.push_notifications
                            ? "text-muted-foreground"
                            : "text-muted-foreground/70"
                        }
                      >
                        {tCommon("pushNotifications")}: {server.push_notifications ? tCommon("on") : tCommon("off")}
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

export function MCPSetting({ servers, loading, onToggleEnabled, onDelete, onAdd }: MCPSettingProps) {
  const t = useTranslations("settings");
  const tCommon = useTranslations("common");
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
      toast.error(t("enterMcpConfig"));
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
            {t("mcp")}
            <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
              <DialogTrigger asChild>
                <Button type="button" size="xs" className="cursor-pointer">
                  {t("addServer")}
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle className="text-foreground">{t("addMcpServer")}</DialogTitle>
                  <DialogDescription className="text-muted-foreground">
                    {t("mcpAddDescription")}
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
                      {tCommon("cancel")}
                    </Button>
                  </DialogClose>
                  <Button className="cursor-pointer" onClick={handleAdd} disabled={adding}>
                    {adding && <Loader2 className="animate-spin" />}
                    {tCommon("add")}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </FieldLegend>
          <FieldDescription className="text-sm">{t("mcpAddDescription")}</FieldDescription>

          {/* 加载态 */}
          {loading && (
            <div className="flex justify-center py-8">
              <Loader2 className="text-muted-foreground size-6 animate-spin" />
            </div>
          )}

          {/* 空态 */}
          {!loading && servers.length === 0 && (
            <div className="text-muted-foreground py-8 text-center text-sm">{t("noMcpServers")}</div>
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
                        {!server.enabled && <Badge>{tCommon("disabled")}</Badge>}
                      </div>
                      <div className="flex items-center justify-center gap-2">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          className="cursor-pointer"
                          onClick={() => onDelete(server.server_name)}
                        >
                          <IconDelete />
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
                        <IconTool size={12} />
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
  labelKey: "common" | "models" | "skills" | "memory" | "integrations";
}> = [
  { key: "common-setting", icon: Settings, labelKey: "common" },
  { key: "models-setting", icon: IconModel, labelKey: "models" },
  { key: "skills-setting", icon: IconSkill, labelKey: "skills" },
  { key: "memory-setting", icon: IconMemory, labelKey: "memory" },
  { key: "integrations-setting", icon: IconIntegration, labelKey: "integrations" },
];

export type SettingsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialTab?: SettingTab;
};

export function SettingsDialog({
  open,
  onOpenChange,
  initialTab = "common-setting",
}: SettingsDialogProps) {
  const t = useTranslations("settings");
  const tCommon = useTranslations("common");
  const [activeSetting, setActiveSetting] = useState<SettingTab>(initialTab);

  useEffect(() => {
    if (open) {
      setActiveSetting(initialTab);
    }
  }, [open, initialTab]);

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
  } = useOpenCitadelSettings(open, activeSetting);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="!max-w-[920px] shadow-[var(--shadow-panel)]">
        <DialogHeader className="border-border/70 border-b pb-4">
          <DialogTitle className="text-foreground">{t("title")}</DialogTitle>
          <DialogDescription className="text-muted-foreground">
            {t("description")}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-row gap-4">
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
                  <span className="truncate">{t(menu.labelKey)}</span>
                </Button>
              ))}
            </div>
          </div>

          <Separator orientation="vertical" />

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
            {activeSetting === "integrations-setting" && (
              <div className="space-y-6 px-1">
                <MCPSetting
                  servers={mcpServers}
                  loading={loadingMCP}
                  onToggleEnabled={handleMCPToggle}
                  onDelete={handleMCPDelete}
                  onAdd={handleMCPAdd}
                />
                <A2ASetting
                  servers={a2aServers}
                  loading={loadingA2A}
                  onToggleEnabled={handleA2AToggle}
                  onDelete={handleA2ADelete}
                  onAdd={handleA2AAdd}
                />
              </div>
            )}
          </div>
        </div>

        {showFooterSave && (
          <DialogFooter className="border-t pt-4">
            <Button
              variant="outline"
              className="cursor-pointer"
              onClick={() => onOpenChange(false)}
            >
              {tCommon("cancel")}
            </Button>
            <Button className="cursor-pointer" disabled={saving} onClick={handleSave}>
              {saving && <Loader2 className="animate-spin" />}
              {tCommon("save")}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
