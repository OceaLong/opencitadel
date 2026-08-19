"use client";

import { useTranslations } from "next-intl";
import type { ComponentType } from "react";

import { StatusBadge } from "@/components/status-badge";

import type { ToolEvent } from "@/lib/api/types";

import { A2aTool } from "./a2a-tool";
import { BashTool } from "./bash-tool";
import { BrowserTool } from "./browser-tool";
import { DefaultTool } from "./default-tool";
import { FileTool } from "./file-tool";
import { McpTool } from "./mcp-tool";
import { MessageTool } from "./message-tool";
import { SearchTool } from "./search-tool";
import type { ToolKind } from "./utils";
import { getFriendlyToolLabel, getToolKind } from "./utils";

export type ToolUseProps = {
  data?: ToolEvent | null;
  onClick?: () => void;
};

const TOOL_COMPONENTS: Record<ToolKind, ComponentType<{ label: string; onClick?: () => void }>> = {
  message: MessageTool,
  bash: BashTool,
  file: FileTool,
  search: SearchTool,
  browser: BrowserTool,
  mcp: McpTool,
  a2a: A2aTool,
  default: DefaultTool,
};

export function ToolUse({ data, onClick }: ToolUseProps) {
  const t = useTranslations("toolUse");
  const label = getFriendlyToolLabel(data);
  const kind = getToolKind(data);
  const Component = TOOL_COMPONENTS[kind];
  const status = data?.status;
  const statusLabel =
    status === "calling"
      ? t("statusRunning")
      : status === "called"
        ? t("statusCalled")
        : status === "error"
          ? t("statusError")
          : status;
  return (
    <div className="inline-flex max-w-full items-center gap-2">
      <Component label={label} onClick={onClick} />
      {status ? (
        <StatusBadge
          variant={status === "error" ? "destructive" : status === "called" ? "success" : "info"}
          className="uppercase tracking-wide"
        >
          {statusLabel}
        </StatusBadge>
      ) : null}
    </div>
  );
}
