"use client";

import { useEffect, useRef, useState } from "react";
import {
  Bot,
  LayoutGrid,
  LayoutList,
  Loader2,
  Pencil,
  Settings,
  Shield,
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

import { AgentSettings } from "@/components/settings/agent-settings";
import { GeneralSettings } from "@/components/settings/general-settings";
import { HitlSettings } from "@/components/settings/hitl-settings";
import { McpServerForm, type McpServerFormHandle } from "@/components/settings/mcp-server-form";
import { MemorySettings } from "@/components/settings/memory-settings";
import { ModelsSettings } from "@/components/settings/models-settings";
import { RuntimeSettings } from "@/components/settings/runtime-settings";
import { ServiceKeysSettings } from "@/components/settings/service-keys-settings";
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
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { type SettingTab, useOpenCitadelSettings } from "@/hooks/use-open-citadel-settings";
import { useIsMobile } from "@/hooks/use-mobile";
import type { ListA2AServerItem, ListMCPServerItem, MCPServerConfig } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

// ==================== A2A Agent 配置 ====================

type A2ASettingProps = {
  servers: ListA2AServerItem[];
  loading: boolean;
  onToggleEnabled: (id: string, enabled: boolean) => void;
  onDelete: (id: string) => void;
  onAdd: (baseUrl: string) => Promise<boolean>;
  readOnly?: boolean;
};

export function A2ASetting({
  servers,
  loading,
  onToggleEnabled,
  onDelete,
  onAdd,
  readOnly = false,
}: A2ASettingProps) {
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
            {!readOnly ? (
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
                          placeholder={t("a2aUrlPlaceholder")}
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
            ) : null}
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
                        {!readOnly ? (
                          <>
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
                          </>
                        ) : null}
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
  onEdit: (serverName: string, config: MCPServerConfig) => Promise<boolean>;
  readOnly?: boolean;
  isAdmin?: boolean;
};

function mcpConnectionStatusLabel(
  server: ListMCPServerItem,
  t: ReturnType<typeof useTranslations<"settings">>,
  disabledLabel: string,
): { label: string; variant: "default" | "secondary" | "destructive" | "outline" } {
  const status = server.connection_status ?? (server.enabled ? "pending" : "disabled");
  switch (status) {
    case "connected":
      return { label: t("mcpStatusConnected"), variant: "default" };
    case "error":
      return { label: t("mcpStatusError"), variant: "destructive" };
    case "pending":
      return { label: t("mcpStatusPending"), variant: "secondary" };
    default:
      return { label: disabledLabel, variant: "outline" };
  }
}

export function MCPSetting({
  servers,
  loading,
  onToggleEnabled,
  onDelete,
  onAdd,
  onEdit,
  readOnly = false,
  isAdmin = false,
}: MCPSettingProps) {
  const t = useTranslations("settings");
  const tCommon = useTranslations("common");
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [addConfig, setAddConfig] = useState("");
  const [adding, setAdding] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editServer, setEditServer] = useState<ListMCPServerItem | null>(null);
  const editFormRef = useRef<McpServerFormHandle>(null);
  const [editing, setEditing] = useState(false);

  const mcpConfigPlaceholder = isAdmin
    ? `{
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
}`
    : `{
  "mcpServers": {
    "remote-tools": {
      "transport": "streamable_http",
      "url": "https://example.com/mcp"
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

  const openEditDialog = (server: ListMCPServerItem) => {
    setEditServer(server);
    setEditDialogOpen(true);
  };

  const handleEdit = async () => {
    if (!editServer) {
      return;
    }
    const form = editFormRef.current;
    if (!form?.validate()) {
      const errorKey = form?.getValidationError();
      if (errorKey) {
        toast.error(t(errorKey));
      } else {
        const transport = editServer.config?.transport ?? editServer.transport;
        toast.error(t(transport === "stdio" ? "mcpCommandRequired" : "mcpUrlRequired"));
      }
      return;
    }
    const config = form.getConfig();
    if (!config) {
      return;
    }
    setEditing(true);
    try {
      const success = await onEdit(editServer.server_name, config);
      if (success) {
        setEditDialogOpen(false);
        setEditServer(null);
      }
    } finally {
      setEditing(false);
    }
  };

  return (
    <div className="w-full px-1">
      <FieldGroup>
        <FieldSet>
          <FieldLegend className="text-foreground flex w-full items-center justify-between text-lg font-semibold">
            {t("mcp")}
            {!readOnly ? (
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
                    {isAdmin ? t("mcpAddDescription") : t("mcpAddDescriptionNonAdmin")}
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
            ) : null}
            <Dialog
              open={editDialogOpen}
              onOpenChange={(open) => {
                setEditDialogOpen(open);
                if (!open) {
                  setEditServer(null);
                }
              }}
            >
              <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
                <DialogHeader>
                  <DialogTitle className="text-foreground">{t("editMcpServer")}</DialogTitle>
                  <DialogDescription className="text-muted-foreground">
                    {editServer
                      ? `${t("editMcpServerDesc")} (${editServer.server_name})`
                      : t("editMcpServerDesc")}
                  </DialogDescription>
                </DialogHeader>
                {editServer ? (
                  <McpServerForm
                    ref={editFormRef}
                    server={editServer}
                    isAdmin={isAdmin}
                    disabled={editing}
                  />
                ) : null}
                <DialogFooter>
                  <DialogClose asChild>
                    <Button variant="outline" className="cursor-pointer" disabled={editing}>
                      {tCommon("cancel")}
                    </Button>
                  </DialogClose>
                  <Button className="cursor-pointer" onClick={handleEdit} disabled={editing}>
                    {editing && <Loader2 className="animate-spin" />}
                    {tCommon("save")}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </FieldLegend>
          <FieldDescription className="text-sm">
            {isAdmin ? t("mcpAddDescription") : t("mcpAddDescriptionNonAdmin")}
          </FieldDescription>
          <FieldDescription className="text-muted-foreground text-xs">
            {t("mcpStatusRefreshHint")}
          </FieldDescription>

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
              {servers.map((server) => {
                const statusBadge = mcpConnectionStatusLabel(server, t, tCommon("disabled"));
                return (
                <Item key={server.server_name} variant="outline">
                  <ItemContent>
                    <ItemTitle className="text-md text-foreground flex w-full items-center justify-between font-semibold">
                      <div className="flex flex-wrap items-center gap-2">
                        {server.server_name}
                        <Badge>{server.transport}</Badge>
                        <Badge variant={statusBadge.variant}>{statusBadge.label}</Badge>
                      </div>
                      <div className="flex items-center justify-center gap-2">
                        {!readOnly ? (
                          <>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          className="cursor-pointer"
                          onClick={() => openEditDialog(server)}
                        >
                          <Pencil className="size-3.5" />
                        </Button>
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
                          </>
                        ) : null}
                      </div>
                    </ItemTitle>
                    {server.connection_error ? (
                      <ItemDescription className="text-destructive text-xs">
                        {server.connection_error}
                      </ItemDescription>
                    ) : null}
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
              );
              })}
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
  labelKey: "common" | "agent" | "models" | "skills" | "memory" | "integrations" | "hitl" | "runtime";
  adminOnly?: boolean;
}> = [
  { key: "common-setting", icon: Settings, labelKey: "common" },
  { key: "agent-setting", icon: Bot, labelKey: "agent" },
  { key: "models-setting", icon: IconModel, labelKey: "models" },
  { key: "skills-setting", icon: IconSkill, labelKey: "skills" },
  { key: "memory-setting", icon: IconMemory, labelKey: "memory" },
  { key: "integrations-setting", icon: IconIntegration, labelKey: "integrations" },
  { key: "hitl-setting", icon: Shield, labelKey: "hitl" },
  { key: "runtime-setting", icon: LayoutGrid, labelKey: "runtime", adminOnly: true },
];

function SettingsMenuButtons({
  menus,
  activeSetting,
  onSelect,
  layout,
}: {
  menus: typeof SETTING_MENUS;
  activeSetting: SettingTab;
  onSelect: (tab: SettingTab) => void;
  layout: "sidebar" | "tabs";
}) {
  const t = useTranslations("settings");

  return menus.map((menu) => (
    <Button
      key={menu.key}
      variant={activeSetting === menu.key ? "default" : "ghost"}
      className={cn(
        "cursor-pointer text-sm",
        layout === "sidebar" ? "justify-start px-2" : "h-9 shrink-0 rounded-full px-3",
      )}
      onClick={() => onSelect(menu.key)}
    >
      <menu.icon className="size-4" />
      <span className="truncate">{t(menu.labelKey)}</span>
    </Button>
  ));
}

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
  const { user } = useAuth();
  const isAdmin = user?.global_role === "admin";
  const [activeSetting, setActiveSetting] = useState<SettingTab>(initialTab);

  useEffect(() => {
    if (open) {
      setActiveSetting(initialTab);
    }
  }, [open, initialTab]);

  const showFooterSave = activeSetting === "agent-setting";
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
    handleMCPEdit,
    handleA2AToggle,
    handleA2ADelete,
    handleA2AAdd,
  } = useOpenCitadelSettings(open, activeSetting);
  const { isMobile } = useIsMobile();
  const visibleMenus = SETTING_MENUS.filter((menu) => !menu.adminOnly || isAdmin);

  const settingsContent = (
    <>
      {loadingConfig && activeSetting === "agent-setting" ? (
        <div className="flex h-full items-center justify-center">
          <Loader2 className="text-muted-foreground size-6 animate-spin" />
        </div>
      ) : (
        <>
          {activeSetting === "common-setting" && <GeneralSettings />}
          {activeSetting === "agent-setting" && (
            <AgentSettings config={agentConfig} onChange={setAgentConfig} readOnly={false} />
          )}
          {activeSetting === "runtime-setting" && isAdmin && (
            <RuntimeSettings isAdmin={isAdmin} />
          )}
        </>
      )}
      {activeSetting === "models-setting" && <ModelsSettings embedded isAdmin={isAdmin} userId={user?.id} />}
      {activeSetting === "skills-setting" && <SkillsSettings embedded isAdmin={isAdmin} userId={user?.id} />}
      {activeSetting === "memory-setting" && <MemorySettings embedded />}
      {activeSetting === "hitl-setting" && <HitlSettings isAdmin={isAdmin} />}
      {activeSetting === "integrations-setting" && (
        <div className="space-y-6 px-1">
          <MCPSetting
            servers={mcpServers}
            loading={loadingMCP}
            onToggleEnabled={handleMCPToggle}
            onDelete={handleMCPDelete}
            onAdd={handleMCPAdd}
            onEdit={handleMCPEdit}
            readOnly={false}
            isAdmin={isAdmin}
          />
          <A2ASetting
            servers={a2aServers}
            loading={loadingA2A}
            onToggleEnabled={handleA2AToggle}
            onDelete={handleA2ADelete}
            onAdd={handleA2AAdd}
            readOnly={false}
          />
          <ServiceKeysSettings />
        </div>
      )}
    </>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "flex flex-col overflow-hidden shadow-[var(--shadow-panel)]",
          "h-[100dvh] max-h-[100dvh] w-full max-w-full rounded-none",
          "md:h-[640px] md:max-h-[90vh] md:!max-w-[920px] md:rounded-lg",
        )}
      >
        <DialogHeader className="border-border/70 shrink-0 border-b pb-4">
          <DialogTitle className="text-foreground">{t("title")}</DialogTitle>
          <DialogDescription className="text-muted-foreground">
            {t("description")}
          </DialogDescription>
        </DialogHeader>

        {isMobile ? (
          <div className="flex min-h-0 flex-1 flex-col gap-3">
            <ScrollArea className="w-full shrink-0 whitespace-nowrap">
              <div className="flex w-max gap-2 pb-1">
                <SettingsMenuButtons
                  menus={visibleMenus}
                  activeSetting={activeSetting}
                  onSelect={setActiveSetting}
                  layout="tabs"
                />
              </div>
            </ScrollArea>
            <div className="scrollbar-hide min-h-0 flex-1 overflow-y-auto">{settingsContent}</div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-row gap-4">
            <div className="w-[168px] shrink-0">
              <div className="flex flex-col gap-0">
                <SettingsMenuButtons
                  menus={visibleMenus}
                  activeSetting={activeSetting}
                  onSelect={setActiveSetting}
                  layout="sidebar"
                />
              </div>
            </div>

            <Separator orientation="vertical" />

            <div className="scrollbar-hide h-full min-h-0 flex-1 overflow-y-auto">{settingsContent}</div>
          </div>
        )}

        <DialogFooter
          className={cn(
            "h-[56px] shrink-0 items-center",
            showFooterSave && "border-t pt-4",
          )}
        >
          {showFooterSave && (
            <>
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
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
