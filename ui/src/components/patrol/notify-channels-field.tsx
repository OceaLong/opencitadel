"use client";

import { useTranslations } from "next-intl";
import { Plus, Trash2 } from "lucide-react";

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

import type { MCPServer } from "@/lib/api";
import type { PatrolNotifyChannel } from "@/lib/api/types";

/** 新增渠道的空白模板（未用字段保持空字符串，对齐后端 schema 默认值）。 */
export function emptyNotifyChannel(): PatrolNotifyChannel {
  return { type: "mcp", server_id: "", channel_arg: "", url: "", secret: "", address: "" };
}

/**
 * 巡检 Pack 的 notify_channels 表单：支持 mcp / webhook / email 三种类型，
 * 按类型渲染对应字段。受控组件，状态由 PackWizard 持有。
 */
export function NotifyChannelsField({
  value,
  onChange,
  servers,
}: {
  value: PatrolNotifyChannel[];
  onChange: (channels: PatrolNotifyChannel[]) => void;
  servers: MCPServer[];
}) {
  const t = useTranslations("patrol");

  const typeLabels: Record<PatrolNotifyChannel["type"], string> = {
    mcp: t("notify.typeMcp"),
    webhook: t("notify.typeWebhook"),
    email: t("notify.typeEmail"),
  };

  const updateChannel = (index: number, patch: Partial<PatrolNotifyChannel>) => {
    onChange(value.map((channel, i) => (i === index ? { ...channel, ...patch } : channel)));
  };

  return (
    <div className="grid gap-3">
      {value.length === 0 ? (
        <p className="text-muted-foreground text-xs">{t("notify.empty")}</p>
      ) : (
        value.map((channel, index) => (
          <div key={index} className="grid gap-3 rounded-lg border p-3">
            <div className="flex items-end justify-between gap-3">
              <div className="grid flex-1 gap-2">
                <Label htmlFor={`notify-type-${index}`}>{t("notify.typeLabel")}</Label>
                <Select
                  value={channel.type}
                  onValueChange={(type) =>
                    updateChannel(index, { type: type as PatrolNotifyChannel["type"] })
                  }
                >
                  <SelectTrigger id={`notify-type-${index}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="mcp">{typeLabels.mcp}</SelectItem>
                    <SelectItem value="webhook">{typeLabels.webhook}</SelectItem>
                    <SelectItem value="email">{typeLabels.email}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={t("notify.remove")}
                title={t("notify.remove")}
                onClick={() => onChange(value.filter((_, i) => i !== index))}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
            {channel.type === "mcp" && (
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-2">
                  <Label htmlFor={`notify-server-${index}`}>{t("notify.serverLabel")}</Label>
                  <Select
                    value={channel.server_id || undefined}
                    onValueChange={(serverId) => updateChannel(index, { server_id: serverId })}
                  >
                    <SelectTrigger id={`notify-server-${index}`}>
                      <SelectValue placeholder={t("notify.serverPlaceholder")} />
                    </SelectTrigger>
                    <SelectContent>
                      {servers.map((server) => (
                        <SelectItem key={server.id} value={server.id}>
                          {server.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor={`notify-channel-arg-${index}`}>
                    {t("notify.channelArgLabel")}
                  </Label>
                  <Input
                    id={`notify-channel-arg-${index}`}
                    value={channel.channel_arg}
                    translate="no"
                    placeholder="#ops-alerts"
                    onChange={(event) => updateChannel(index, { channel_arg: event.target.value })}
                  />
                </div>
              </div>
            )}
            {channel.type === "webhook" && (
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-2">
                  <Label htmlFor={`notify-url-${index}`}>{t("notify.urlLabel")}</Label>
                  <Input
                    id={`notify-url-${index}`}
                    value={channel.url}
                    translate="no"
                    placeholder="https://"
                    onChange={(event) => updateChannel(index, { url: event.target.value })}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor={`notify-secret-${index}`}>{t("notify.secretLabel")}</Label>
                  <Input
                    id={`notify-secret-${index}`}
                    type="password"
                    value={channel.secret}
                    onChange={(event) => updateChannel(index, { secret: event.target.value })}
                  />
                </div>
              </div>
            )}
            {channel.type === "email" && (
              <div className="grid gap-2">
                <Label htmlFor={`notify-address-${index}`}>{t("notify.addressLabel")}</Label>
                <Input
                  id={`notify-address-${index}`}
                  type="email"
                  value={channel.address}
                  translate="no"
                  placeholder="ops@example.com"
                  onChange={(event) => updateChannel(index, { address: event.target.value })}
                />
              </div>
            )}
          </div>
        ))
      )}
      <div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onChange([...value, emptyNotifyChannel()])}
        >
          <Plus className="size-4" />
          {t("notify.add")}
        </Button>
      </div>
    </div>
  );
}
