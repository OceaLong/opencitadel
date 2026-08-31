"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { LayoutGrid, Loader2, Pencil, Settings } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { GeneralSettings } from "@/components/settings/general-settings";
import { InferenceSettings } from "@/components/settings/inference-settings";
import { McpServerForm, type McpServerFormHandle } from "@/components/settings/mcp-server-form";
import { MemorySettings } from "@/components/settings/memory-settings";
import { RuntimePolicySettings } from "@/components/settings/runtime-policy-settings";
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
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { useIsMobile } from "@/hooks/use-mobile";
import { type SettingTab, useOpenCitadelSettings } from "@/hooks/use-open-citadel-settings";
import type {
  A2AServer,
  MCPServer,
  UpdateA2AServerRequest,
  UpdateMCPServerRequest,
} from "@/lib/api";
import { IconDelete, IconIntegration, IconMemory, IconModel, IconSkill } from "@/lib/icons";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

function IntegrationStatusLabel({ enabled }: { enabled: boolean }) {
  const t = useTranslations("settings");
  return enabled ? t("integrationStatus.enabled") : t("integrationStatus.disabled");
}

// ==================== A2A Agent 配置 ====================

type A2ASettingProps = {
  servers: A2AServer[];
  loading: boolean;
  onToggleEnabled: (id: string, enabled: boolean) => void;
  onDelete: (id: string) => void;
  onAdd: (baseUrl: string) => Promise<boolean>;
  onEdit: (id: string, body: UpdateA2AServerRequest) => Promise<boolean>;
  readOnly?: boolean;
};

export function A2ASetting({
  servers,
  loading,
  onToggleEnabled,
  onDelete,
  onAdd,
  onEdit,
  readOnly = false,
}: A2ASettingProps) {
  const t = useTranslations("settings");
  const tCommon = useTranslations("common");
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [addUrl, setAddUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [editTarget, setEditTarget] = useState<A2AServer | null>(null);
  const [editUrl, setEditUrl] = useState("");
  const [editVisibility, setEditVisibility] = useState<A2AServer["visibility"]>("private");
  const [saving, setSaving] = useState(false);

  const openEdit = (server: A2AServer) => {
    setEditTarget(server);
    setEditUrl(server.base_url);
    setEditVisibility(server.visibility);
  };

  const handleEdit = async () => {
    if (!editTarget) return;
    if (!editUrl.trim()) {
      toast.error(t("enterAgentUrl"));
      return;
    }
    setSaving(true);
    try {
      const success = await onEdit(editTarget.id, {
        base_url: editUrl.trim(),
        enabled: editTarget.enabled,
        visibility: editVisibility,
      });
      if (success) setEditTarget(null);
    } finally {
      setSaving(false);
    }
  };

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
            <EmptyState title={t("noA2aAgents")} className="py-8" />
          )}

          {/* 列表 */}
          {!loading && servers.length > 0 && (
            <ItemGroup className="gap-3">
              {servers.map((server) => (
                <Item key={server.id} variant="outline">
                  <ItemContent>
                    <ItemTitle className="text-md text-foreground flex w-full items-center justify-between font-semibold">
                      <div className="flex items-center gap-2">
                        {server.base_url}
                        <Badge variant="secondary">
                          <IntegrationStatusLabel enabled={server.enabled} />
                        </Badge>
                      </div>
                      <div className="flex items-center justify-center gap-2">
                        {!readOnly ? (
                          <>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-xs"
                              className="cursor-pointer"
                              onClick={() => openEdit(server)}
                            >
                              <Pencil className="size-4" />
                            </Button>
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
                    <ItemDescription className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <Badge variant="secondary">{server.visibility}</Badge>
                    </ItemDescription>
                  </ItemContent>
                </Item>
              ))}
            </ItemGroup>
          )}
        </FieldSet>
      </FieldGroup>
      <Dialog open={editTarget !== null} onOpenChange={(open) => !open && setEditTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="text-foreground">{t("editRemoteAgent")}</DialogTitle>
            <DialogDescription className="text-muted-foreground">
              {t("a2aEditDescription")}
            </DialogDescription>
          </DialogHeader>
          <form
            className="w-full"
            onSubmit={(e) => {
              e.preventDefault();
              handleEdit();
            }}
          >
            <FieldGroup>
              <FieldSet className="gap-4">
                <Field>
                  <Input
                    id="a2a_edit_base_url"
                    type="url"
                    placeholder={t("a2aUrlPlaceholder")}
                    value={editUrl}
                    onChange={(e) => setEditUrl(e.target.value)}
                    disabled={saving}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="a2a_edit_visibility">{t("visibilityLabel")}</FieldLabel>
                  <Select
                    value={editVisibility}
                    onValueChange={(value) => setEditVisibility(value as A2AServer["visibility"])}
                    disabled={saving}
                  >
                    <SelectTrigger id="a2a_edit_visibility" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="private">{t("visibilityPrivate")}</SelectItem>
                      <SelectItem value="global">{t("visibilityGlobal")}</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              </FieldSet>
            </FieldGroup>
          </form>
          <DialogFooter>
            <Button
              variant="outline"
              className="cursor-pointer"
              disabled={saving}
              onClick={() => setEditTarget(null)}
            >
              {tCommon("cancel")}
            </Button>
            <Button className="cursor-pointer" onClick={handleEdit} disabled={saving}>
              {saving && <Loader2 className="animate-spin" />}
              {tCommon("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ==================== MCP 服务器 ====================

type MCPSettingProps = {
  servers: MCPServer[];
  loading: boolean;
  onToggleEnabled: (serverName: string, enabled: boolean) => void;
  onDelete: (serverName: string) => void;
  onAdd: (config: string) => Promise<boolean>;
  onEdit: (serverId: string, config: UpdateMCPServerRequest) => Promise<boolean>;
  readOnly?: boolean;
  isAdmin?: boolean;
};

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
  const [editServer, setEditServer] = useState<MCPServer | null>(null);
  const editFormRef = useRef<McpServerFormHandle>(null);
  const [editing, setEditing] = useState(false);

  const mcpConfigPlaceholder = isAdmin
    ? `{
  "name": "local-tools",
  "transport": "stdio",
  "command": "uvx",
  "args": ["example-mcp-server"],
  "enabled": true,
  "visibility": "global"
}`
    : `{
  "name": "remote-tools",
  "transport": "streamable_http",
  "url": "https://example.com/mcp",
  "enabled": true,
  "visibility": "private"
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

  const openEditDialog = (server: MCPServer) => {
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
        const transport = editServer.transport;
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
      const success = await onEdit(editServer.id, config);
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
                      ? `${t("editMcpServerDesc")} (${editServer.name})`
                      : t("editMcpServerDesc")}
                  </DialogDescription>
                </DialogHeader>
                {editServer ? (
                  <McpServerForm
                    key={editServer.id}
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
          {/* 加载态 */}
          {loading && (
            <div className="flex justify-center py-8">
              <Loader2 className="text-muted-foreground size-6 animate-spin" />
            </div>
          )}

          {/* 空态 */}
          {!loading && servers.length === 0 && (
            <EmptyState title={t("noMcpServers")} className="py-8" />
          )}

          {/* 列表 */}
          {!loading && servers.length > 0 && (
            <ItemGroup className="gap-3">
              {servers.map((server) => {
                return (
                  <Item key={server.id} variant="outline">
                    <ItemContent>
                      <ItemTitle className="text-md text-foreground flex w-full items-center justify-between font-semibold">
                        <div className="flex flex-wrap items-center gap-2">
                          {server.name}
                          <Badge>{server.transport}</Badge>
                          <Badge variant="secondary">{server.visibility}</Badge>
                          <Badge variant="secondary">
                            <IntegrationStatusLabel enabled={server.enabled} />
                          </Badge>
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
                      {server.description ? (
                        <ItemDescription>{server.description}</ItemDescription>
                      ) : null}
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
  labelKey: "common" | "inference" | "skills" | "memory" | "integrations" | "runtime";
  adminOnly?: boolean;
}> = [
  { key: "common-setting", icon: Settings, labelKey: "common" },
  { key: "inference-setting", icon: IconModel, labelKey: "inference" },
  { key: "skills-setting", icon: IconSkill, labelKey: "skills" },
  { key: "memory-setting", icon: IconMemory, labelKey: "memory" },
  { key: "integrations-setting", icon: IconIntegration, labelKey: "integrations" },
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
  const { user } = useAuth();
  const isAdmin = user?.global_role === "admin";
  const [activeSetting, setActiveSetting] = useState<SettingTab>(initialTab);

  const {
    mcpServers,
    a2aServers,
    loadingMCP,
    loadingA2A,
    handleMCPToggle,
    handleMCPDelete,
    handleMCPAdd,
    handleMCPEdit,
    handleA2AToggle,
    handleA2ADelete,
    handleA2AAdd,
    handleA2AEdit,
  } = useOpenCitadelSettings(open);
  const { isMobile } = useIsMobile();
  const visibleMenus = SETTING_MENUS.filter((menu) => !menu.adminOnly || isAdmin);

  const settingsContent = (
    <>
      {activeSetting === "common-setting" && <GeneralSettings />}
      {activeSetting === "runtime-setting" && isAdmin && <RuntimePolicySettings />}
      {activeSetting === "inference-setting" && (
        <InferenceSettings embedded isAdmin={isAdmin} userId={user?.id} />
      )}
      {activeSetting === "skills-setting" && (
        <SkillsSettings embedded isAdmin={isAdmin} userId={user?.id} />
      )}
      {activeSetting === "memory-setting" && <MemorySettings embedded />}
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
            onEdit={handleA2AEdit}
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
          "shadow-panel flex flex-col overflow-hidden",
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

            <div className="scrollbar-hide h-full min-h-0 flex-1 overflow-y-auto">
              {settingsContent}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
