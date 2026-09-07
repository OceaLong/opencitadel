"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
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
import { notificationsApi } from "@/lib/api/notifications";
import type { PatrolNotifyChannel } from "@/lib/api/types";

/** 新增渠道的空白模板（未用字段保持空字符串，对齐后端 schema 默认值）。 */
export function emptyNotifyChannel(): PatrolNotifyChannel {
  return {
    type: "mcp",
    server_id: "",
    tool_name: "",
    message_arg: "text",
    arguments: {},
    url: "",
    secret: "",
    address: "",
  };
}

/**
 * 巡检 Pack 的 notify_channels 表单：支持 mcp / webhook / email 三种类型，
 * 按类型渲染对应字段。受控组件，状态由 PackWizard 持有。
 */
export function NotifyChannelsField({
  value,
  onChange,
  servers,
  onValidityChange,
}: {
  value: PatrolNotifyChannel[];
  onChange: (channels: PatrolNotifyChannel[]) => void;
  servers: MCPServer[];
  onValidityChange?: (valid: boolean) => void;
}) {
  const t = useTranslations("patrol");
  const zh = useLocale().startsWith("zh");
  const [checking, setChecking] = useState<number | null>(null);
  const [checkResults, setCheckResults] = useState<Record<number, boolean>>({});
  const [testResults, setTestResults] = useState<
    Record<number, { id: string; status: string; error?: string | null }>
  >({});
  const pendingIds = Object.values(testResults)
    .filter((item) => !["sent", "failed"].includes(item.status))
    .map((item) => item.id)
    .sort()
    .join(",");
  useEffect(() => {
    if (!pendingIds) return;
    let cancelled = false;
    const timer = setInterval(() => {
      void Promise.all(pendingIds.split(",").map((id) => notificationsApi.delivery(id)))
        .then((deliveries) => {
          if (cancelled) return;
          setTestResults((prev) =>
            Object.fromEntries(
              Object.entries(prev).map(([index, item]) => {
                const result = deliveries.find((row) => row.id === item.id);
                return [
                  index,
                  result ? { id: item.id, status: result.status, error: result.last_error } : item,
                ];
              }),
            ),
          );
        })
        .catch(() => {
          /* Keep the durable delivery visible; next poll retries. */
        });
    }, 3000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pendingIds]);
  const [argumentDrafts, setArgumentDrafts] = useState<Record<number, string>>({});
  const [invalidArguments, setInvalidArguments] = useState<Set<number>>(new Set());
  const setArgumentValidity = (index: number, valid: boolean) => {
    const next = new Set(invalidArguments);
    if (valid) next.delete(index);
    else next.add(index);
    setInvalidArguments(next);
    onValidityChange?.(next.size === 0);
  };

  const typeLabels: Record<PatrolNotifyChannel["type"], string> = {
    mcp: t("notify.typeMcp"),
    webhook: t("notify.typeWebhook"),
    email: t("notify.typeEmail"),
  };

  const updateChannel = (index: number, patch: Partial<PatrolNotifyChannel>) => {
    setCheckResults((prev) => {
      const next = { ...prev };
      delete next[index];
      return next;
    });
    if (patch.type && patch.type !== "mcp") setArgumentValidity(index, true);
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
                onClick={() => {
                  const invalid = new Set(
                    [...invalidArguments]
                      .filter((i) => i !== index)
                      .map((i) => (i > index ? i - 1 : i)),
                  );
                  setInvalidArguments(invalid);
                  setArgumentDrafts(
                    Object.fromEntries(
                      Object.entries(argumentDrafts)
                        .filter(([i]) => Number(i) !== index)
                        .map(([i, draft]) => [
                          Number(i) > index ? Number(i) - 1 : Number(i),
                          draft,
                        ]),
                    ),
                  );
                  onValidityChange?.(invalid.size === 0);
                  onChange(value.filter((_, i) => i !== index));
                }}
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
                  <Label>{zh ? "发送工具名称" : "Send tool name"}</Label>
                  <Input
                    value={channel.tool_name}
                    placeholder="mcp_server_send_message"
                    translate="no"
                    onChange={(event) => updateChannel(index, { tool_name: event.target.value })}
                  />
                </div>
                <div className="grid gap-2">
                  <Label>{zh ? "消息参数名" : "Message parameter"}</Label>
                  <Input
                    value={channel.message_arg}
                    onChange={(event) => updateChannel(index, { message_arg: event.target.value })}
                  />
                </div>
                <div className="grid gap-2">
                  <Label>{zh ? "幂等参数名（可选）" : "Idempotency parameter (optional)"}</Label>
                  <Input
                    value={channel.idempotency_arg ?? ""}
                    onChange={(event) =>
                      updateChannel(index, { idempotency_arg: event.target.value })
                    }
                  />
                </div>
                <div className="grid gap-2">
                  <Label>{zh ? "固定参数（JSON）" : "Fixed arguments (JSON)"}</Label>
                  <Input
                    aria-invalid={invalidArguments.has(index)}
                    value={argumentDrafts[index] ?? JSON.stringify(channel.arguments)}
                    placeholder={'{"room": "ops"}'}
                    translate="no"
                    onChange={(event) => {
                      setArgumentDrafts((prev) => ({ ...prev, [index]: event.target.value }));
                      try {
                        const parsed: unknown = JSON.parse(event.target.value);
                        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
                          throw new Error();
                        event.target.setCustomValidity("");
                        setArgumentValidity(index, true);
                        updateChannel(index, { arguments: parsed as Record<string, unknown> });
                      } catch {
                        setArgumentValidity(index, false);
                        event.target.setCustomValidity(
                          zh ? "请输入 JSON 对象" : "Enter a JSON object",
                        );
                      }
                    }}
                  />
                  {invalidArguments.has(index) && (
                    <p role="alert" className="text-destructive text-sm">
                      {zh ? "请输入有效的 JSON 对象" : "Enter a valid JSON object"}
                    </p>
                  )}
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
            <div className="flex flex-wrap items-center gap-3">
              <Button
                variant="outline"
                size="sm"
                disabled={checking !== null || invalidArguments.has(index)}
                onClick={async () => {
                  setChecking(index);
                  try {
                    await notificationsApi.validateChannel(channel);
                    setCheckResults((prev) => ({ ...prev, [index]: true }));
                  } catch {
                    setCheckResults((prev) => ({ ...prev, [index]: false }));
                  } finally {
                    setChecking(null);
                  }
                }}
              >
                {zh ? "检查配置（不发送）" : "Validate configuration (no send)"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={checking !== null || invalidArguments.has(index)}
                onClick={async () => {
                  setChecking(index);
                  try {
                    const result = await notificationsApi.testChannel(channel, crypto.randomUUID());
                    setTestResults((prev) => ({
                      ...prev,
                      [index]: { id: result.delivery_id, status: result.status },
                    }));
                  } catch {
                    setCheckResults((prev) => ({ ...prev, [index]: false }));
                  } finally {
                    setChecking(null);
                  }
                }}
              >
                {zh ? "发送测试通知" : "Send test notification"}
              </Button>
              {checkResults[index] !== undefined && (
                <span
                  role="status"
                  className={checkResults[index] ? "text-sm" : "text-destructive text-sm"}
                >
                  {checkResults[index]
                    ? zh
                      ? "配置有效，未发送消息"
                      : "Configuration valid; no message sent"
                    : zh
                      ? "配置无效或服务不可用，请检查字段"
                      : "Invalid configuration or unavailable service; check the fields"}
                </span>
              )}
              {testResults[index] && (
                <span role="status" className="text-sm">
                  {zh ? "测试投递：" : "Test delivery: "}
                  {testResults[index].status}
                  {testResults[index].error && (
                    <span className="text-destructive"> · {testResults[index].error}</span>
                  )}
                </span>
              )}
            </div>
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
