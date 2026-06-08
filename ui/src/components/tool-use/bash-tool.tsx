"use client";

import { Terminal } from "lucide-react";

import { ToolBadge } from "./tool-badge";

export type BashToolProps = {
  label: string;
  onClick?: () => void;
};

export function BashTool({ label, onClick }: BashToolProps) {
  return <ToolBadge icon={Terminal} label={label} onClick={onClick} />;
}
