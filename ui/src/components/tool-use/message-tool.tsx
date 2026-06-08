"use client";

export type MessageToolProps = {
  label: string;
  onClick?: () => void;
};

export function MessageTool({ label }: MessageToolProps) {
  return <p className="text-foreground min-w-0 text-sm">{label}</p>;
}
