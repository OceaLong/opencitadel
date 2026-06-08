"use client";

import { FileSearch } from "lucide-react";

import { ToolBadge } from "./tool-badge";

export type FileToolProps = {
  label: string;
  onClick?: () => void;
};

export function FileTool({ label, onClick }: FileToolProps) {
  return <ToolBadge icon={FileSearch} label={label} onClick={onClick} />;
}
