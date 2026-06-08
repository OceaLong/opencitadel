"use client";

import { FileSearch, FileText } from "lucide-react";

import { Button } from "@/components/ui/button";

import type { AttachmentFile } from "@/lib/session-events";
import { cn, formatFileSize } from "@/lib/utils";

export type AttachmentsMessageProps = {
  className?: string;
  role: "user" | "assistant";
  files: AttachmentFile[];
  onViewAllFiles?: () => void;
  onFileClick?: (file: AttachmentFile) => void;
};

const CARD_WIDTH = 280;
const CARD_HEIGHT = 72;

function FileCard({
  file,
  sizeLabel,
  role,
  onClick,
}: {
  file: AttachmentFile;
  sizeLabel: string;
  role: "user" | "assistant";
  onClick?: () => void;
}) {
  return (
    <div
      className={cn(
        "border-border/70 bg-card hover:bg-muted/50 flex flex-shrink-0 cursor-pointer items-center gap-3 rounded-xl border p-3 shadow-[var(--shadow-card)] transition-colors",
        role === "user" && "bg-card",
      )}
      style={{ width: CARD_WIDTH, height: CARD_HEIGHT }}
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick?.();
        }
      }}
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-blue-100 text-blue-600">
        <FileText size={18} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-foreground truncate text-sm font-semibold">{file.filename}</p>
        <p className="text-muted-foreground mt-0.5 text-xs">
          {file.extension} · {sizeLabel}
        </p>
      </div>
    </div>
  );
}

export function AttachmentsMessage({
  className,
  role,
  files,
  onViewAllFiles,
  onFileClick,
}: AttachmentsMessageProps) {
  const sizeLabel = (f: AttachmentFile) => f.sizeLabel ?? formatFileSize(f.size);

  if (role === "user") {
    return (
      <div className={cn("flex flex-col flex-wrap items-end justify-end gap-2", className)}>
        <div className="flex max-w-[568px] flex-wrap justify-end gap-2">
          {files.map((file, index) => (
            <FileCard
              key={file.id ? `${file.id}-${index}` : `file-${index}`}
              file={file}
              sizeLabel={sizeLabel(file)}
              role="user"
              onClick={() => onFileClick?.(file)}
            />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className={cn("flex flex-col justify-start", className)}>
      <div className="flex max-w-[600px] flex-wrap items-center gap-3">
        {files.map((file, index) => (
          <FileCard
            key={file.id ? `${file.id}-${index}` : `file-${index}`}
            file={file}
            sizeLabel={sizeLabel(file)}
            role="assistant"
            onClick={() => onFileClick?.(file)}
          />
        ))}
        {onViewAllFiles && (
          <Button
            variant="outline"
            size="sm"
            className="border-border/70 bg-card hover:bg-muted/50 text-muted-foreground shrink-0 cursor-pointer gap-2 rounded-xl border px-3 py-2 shadow-[var(--shadow-card)]"
            style={{ width: CARD_WIDTH, height: CARD_HEIGHT }}
            onClick={onViewAllFiles}
          >
            <FileSearch size={18} className="shrink-0" />
            <span className="text-sm whitespace-nowrap">查看此任务中所有的文件</span>
          </Button>
        )}
      </div>
    </div>
  );
}
