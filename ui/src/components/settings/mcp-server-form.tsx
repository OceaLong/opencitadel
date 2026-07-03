"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { AlertTriangle, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

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
import { Textarea } from "@/components/ui/textarea";

import type { ListMCPServerItem, MCPServerConfig, MCPTransport } from "@/lib/api/types";

type KeyValueRow = { id: string; key: string; value: string };

export type McpServerFormState = {
  transport: MCPTransport;
  description: string;
  serviceUrl: string;
  urlParams: KeyValueRow[];
  headerRows: KeyValueRow[];
  command: string;
  argsText: string;
  envRows: KeyValueRow[];
  urlUndecryptable: boolean;
};

export type McpServerFormHandle = {
  getConfig: () => MCPServerConfig | null;
  validate: () => boolean;
  getValidationError: () => string | null;
};

const HTTP_URL_SCHEME = /^https?:\/\//i;

type McpServerFormProps = {
  server: ListMCPServerItem;
  isAdmin?: boolean;
  disabled?: boolean;
};

const HTTP_TRANSPORTS: MCPTransport[] = ["streamable_http", "sse"];

function newRow(key = "", value = ""): KeyValueRow {
  return { id: crypto.randomUUID(), key, value };
}

function looksEncrypted(url: string | null | undefined): boolean {
  if (!url) return false;
  return url.startsWith("gAAAA");
}

function dictToRows(dict: Record<string, unknown> | null | undefined): KeyValueRow[] {
  if (!dict) return [];
  return Object.keys(dict).map((key) => newRow(key, ""));
}

function parseUrlToForm(url: string | null | undefined): {
  serviceUrl: string;
  urlParams: KeyValueRow[];
  urlUndecryptable: boolean;
} {
  if (!url) {
    return { serviceUrl: "", urlParams: [], urlUndecryptable: false };
  }
  if (looksEncrypted(url)) {
    return { serviceUrl: "", urlParams: [], urlUndecryptable: true };
  }
  try {
    const parsed = new URL(url);
    const serviceUrl = `${parsed.origin}${parsed.pathname}`;
    const urlParams = Array.from(parsed.searchParams.entries()).map(([key]) => newRow(key, ""));
    return { serviceUrl, urlParams, urlUndecryptable: false };
  } catch {
    return { serviceUrl: "", urlParams: [], urlUndecryptable: true };
  }
}

export function mcpConfigToForm(server: ListMCPServerItem): McpServerFormState {
  const config = server.config ?? {};
  const transport = (config.transport ?? server.transport ?? "streamable_http") as MCPTransport;
  const { serviceUrl, urlParams, urlUndecryptable } = parseUrlToForm(config.url ?? null);

  return {
    transport,
    description: config.description ?? "",
    serviceUrl,
    urlParams,
    headerRows: dictToRows(config.headers as Record<string, unknown> | null | undefined),
    command: config.command ?? "",
    argsText: (config.args ?? []).join("\n"),
    envRows: dictToRows(config.env as Record<string, unknown> | null | undefined),
    urlUndecryptable,
  };
}

function rowsToRecord(rows: KeyValueRow[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    result[key] = row.value;
  }
  return result;
}

function buildUrl(serviceUrl: string, urlParams: KeyValueRow[]): string {
  const base = serviceUrl.trim();
  const pairs = urlParams
    .filter((row) => row.key.trim())
    .map((row) => {
      const key = encodeURIComponent(row.key.trim());
      const value = encodeURIComponent(row.value);
      return `${key}=${value}`;
    });
  if (pairs.length === 0) return base;
  return `${base}?${pairs.join("&")}`;
}

export function formToMcpConfig(form: McpServerFormState, enabled: boolean): MCPServerConfig {
  const isHttp = HTTP_TRANSPORTS.includes(form.transport);
  const args = form.argsText
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  if (isHttp) {
    return {
      transport: form.transport,
      enabled,
      description: form.description.trim() || null,
      url: buildUrl(form.serviceUrl, form.urlParams),
      headers: Object.keys(rowsToRecord(form.headerRows)).length
        ? rowsToRecord(form.headerRows)
        : null,
      command: null,
      args: null,
      env: null,
    };
  }

  return {
    transport: form.transport,
    enabled,
    description: form.description.trim() || null,
    command: form.command.trim() || null,
    args: args.length ? args : null,
    env: Object.keys(rowsToRecord(form.envRows)).length ? rowsToRecord(form.envRows) : null,
    url: null,
    headers: null,
  };
}

export const McpServerForm = forwardRef<McpServerFormHandle, McpServerFormProps>(function McpServerForm(
  { server, isAdmin = false, disabled = false },
  ref,
) {
  const t = useTranslations("settings");
  const [form, setForm] = useState<McpServerFormState>(() => mcpConfigToForm(server));
  const [validationError, setValidationError] = useState<string | null>(null);
  const validationErrorRef = useRef<string | null>(null);

  useEffect(() => {
    setForm(mcpConfigToForm(server));
    setValidationError(null);
    validationErrorRef.current = null;
  }, [server]);

  const runValidation = (): boolean => {
    if (HTTP_TRANSPORTS.includes(form.transport)) {
      const serviceUrl = form.serviceUrl.trim();
      if (!serviceUrl) {
        validationErrorRef.current = "mcpUrlRequired";
        setValidationError("mcpUrlRequired");
        return false;
      }
      if (!HTTP_URL_SCHEME.test(serviceUrl)) {
        validationErrorRef.current = "mcpUrlInvalidScheme";
        setValidationError("mcpUrlInvalidScheme");
        return false;
      }
      if (form.urlUndecryptable) {
        const missingKeyValue = form.urlParams.some(
          (row) => row.key.trim() && !row.value.trim(),
        );
        if (missingKeyValue) {
          validationErrorRef.current = "mcpParamValueRequiredWhenUndecryptable";
          setValidationError("mcpParamValueRequiredWhenUndecryptable");
          return false;
        }
      }
    } else if (form.transport === "stdio") {
      if (!form.command.trim()) {
        validationErrorRef.current = "mcpCommandRequired";
        setValidationError("mcpCommandRequired");
        return false;
      }
    }
    validationErrorRef.current = null;
    setValidationError(null);
    return true;
  };

  useImperativeHandle(ref, () => ({
    validate: () => runValidation(),
    getValidationError: () => validationErrorRef.current,
    getConfig: () => formToMcpConfig(form, server.enabled),
  }));

  const isHttp = HTTP_TRANSPORTS.includes(form.transport);

  const updateRow = (
    field: "urlParams" | "headerRows" | "envRows",
    id: string,
    patch: Partial<KeyValueRow>,
  ) => {
    setForm((prev) => ({
      ...prev,
      [field]: prev[field].map((row) => (row.id === id ? { ...row, ...patch } : row)),
    }));
  };

  const addRow = (field: "urlParams" | "headerRows" | "envRows") => {
    setForm((prev) => ({ ...prev, [field]: [...prev[field], newRow()] }));
  };

  const removeRow = (field: "urlParams" | "headerRows" | "envRows", id: string) => {
    setForm((prev) => ({ ...prev, [field]: prev[field].filter((row) => row.id !== id) }));
  };

  const renderKeyValueRows = (
    field: "urlParams" | "headerRows" | "envRows",
    keyLabel: string,
    valueLabel: string,
    addLabel: string,
    valuePlaceholder?: string,
  ) => (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <Label>{field === "urlParams" ? t("mcpUrlParams") : keyLabel}</Label>
        <Button
          type="button"
          variant="outline"
          size="xs"
          disabled={disabled}
          onClick={() => addRow(field)}
        >
          <Plus className="size-3.5" />
          {addLabel}
        </Button>
      </div>
      {form[field].length === 0 ? (
        <p className="text-muted-foreground text-xs">{t("mcpNoParams")}</p>
      ) : (
        <div className="space-y-2">
          {form[field].map((row) => (
            <div key={row.id} className="flex items-end gap-2">
              <div className="grid flex-1 gap-1">
                <Label className="text-xs">{keyLabel}</Label>
                <Input
                  value={row.key}
                  disabled={disabled}
                  onChange={(e) => updateRow(field, row.id, { key: e.target.value })}
                  placeholder={t("mcpParamKey")}
                />
              </div>
              <div className="grid flex-1 gap-1">
                <Label className="text-xs">{valueLabel}</Label>
                <Input
                  type="password"
                  value={row.value}
                  disabled={disabled}
                  onChange={(e) => updateRow(field, row.id, { value: e.target.value })}
                  placeholder={
                    valuePlaceholder ??
                    (field === "urlParams"
                      ? t("mcpParamValueLeaveBlank")
                      : field === "headerRows"
                        ? t("mcpHeaderValueLeaveBlank")
                        : t("mcpEnvValueLeaveBlank"))
                  }
                />
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                disabled={disabled}
                onClick={() => removeRow(field, row.id)}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className="max-h-[60vh] space-y-4 overflow-y-auto pr-1">
      {form.urlUndecryptable && isHttp ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-900 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>{t("mcpUrlUndecryptable")}</span>
        </div>
      ) : null}

      <div className="space-y-2">
        <Label>{t("mcpTransport")}</Label>
        <Select
          value={form.transport}
          disabled={disabled}
          onValueChange={(value) =>
            setForm((prev) => ({ ...prev, transport: value as MCPTransport }))
          }
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="streamable_http">streamable_http</SelectItem>
            <SelectItem value="sse">sse</SelectItem>
            {isAdmin ? <SelectItem value="stdio">stdio</SelectItem> : null}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label>{t("mcpDescription")}</Label>
        <Input
          value={form.description}
          disabled={disabled}
          onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
          placeholder={t("mcpDescriptionPlaceholder")}
        />
      </div>

      {isHttp ? (
        <>
          <div className="space-y-2">
            <Label>{t("mcpServiceUrl")}</Label>
            <Input
              value={form.serviceUrl}
              disabled={disabled}
              onChange={(e) => {
                validationErrorRef.current = null;
                setValidationError(null);
                setForm((prev) => ({ ...prev, serviceUrl: e.target.value }));
              }}
              placeholder="https://mcp.example.com/mcp"
              aria-invalid={validationError === "mcpUrlRequired" || validationError === "mcpUrlInvalidScheme"}
            />
            {validationError === "mcpUrlRequired" || validationError === "mcpUrlInvalidScheme" ? (
              <p className="text-destructive text-xs">{t(validationError)}</p>
            ) : null}
          </div>
          {renderKeyValueRows(
            "urlParams",
            t("mcpParamKey"),
            t("mcpParamValueLeaveBlank"),
            t("addParam"),
            form.urlUndecryptable ? t("mcpParamValueRequiredWhenUndecryptable") : undefined,
          )}
          {renderKeyValueRows(
            "headerRows",
            t("mcpHeaderKey"),
            t("mcpHeaderValueLeaveBlank"),
            t("addHeader"),
          )}
        </>
      ) : (
        <>
          <div className="space-y-2">
            <Label>{t("mcpCommand")}</Label>
            <Input
              value={form.command}
              disabled={disabled}
              onChange={(e) => setForm((prev) => ({ ...prev, command: e.target.value }))}
              placeholder="uvx"
            />
          </div>
          <div className="space-y-2">
            <Label>{t("mcpArgs")}</Label>
            <Textarea
              value={form.argsText}
              disabled={disabled}
              onChange={(e) => setForm((prev) => ({ ...prev, argsText: e.target.value }))}
              placeholder={t("mcpArgsPlaceholder")}
              className="min-h-[80px] font-mono text-xs"
            />
          </div>
          {renderKeyValueRows(
            "envRows",
            t("mcpEnvKey"),
            t("mcpEnvValueLeaveBlank"),
            t("addEnv"),
          )}
        </>
      )}
    </div>
  );
});
