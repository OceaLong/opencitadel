"use client";

import type { ComponentType } from "react";

import type { ToolEvent } from "@/lib/api/types";
import { cn } from "@/lib/utils";

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
  const label = getFriendlyToolLabel(data);
  const kind = getToolKind(data);
  const Component = TOOL_COMPONENTS[kind];
  const status = data?.status;
  return (
    <div className="inline-flex max-w-full items-center gap-2">
      <Component label={label} onClick={onClick} />
      {status && (
        <span
          className={cn(
            "rounded-full border px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
            status === "calling" &&
              "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
            status === "called" &&
              "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
            status === "error" && "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300",
          )}
        >
          {status === "calling" ? "running" : status}
        </span>
      )}
    </div>
  );
}
