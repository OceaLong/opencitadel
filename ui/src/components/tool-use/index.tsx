"use client";

import type { ComponentType } from "react";

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

export { A2aTool } from "./a2a-tool";
export { BashTool } from "./bash-tool";
export { BrowserTool } from "./browser-tool";
export { DefaultTool } from "./default-tool";
export { FileTool } from "./file-tool";
export { McpTool } from "./mcp-tool";
export { MessageTool } from "./message-tool";
export { SearchTool } from "./search-tool";
export { ToolBadge } from "./tool-badge";
export type { ToolKind } from "./utils";
export { getFriendlyToolLabel, getToolKind } from "./utils";

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
  return <Component label={label} onClick={onClick} />;
}
