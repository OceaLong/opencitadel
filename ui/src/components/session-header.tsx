"use client";

import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { SessionDebugSheet } from "@/components/session-debug-sheet";
import { SessionMemorySheet } from "@/components/session-memory-sheet";
import { Avatar, AvatarGroupCount } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
import { ScrollArea } from "@/components/ui/scroll-area";

import { fileApi, sessionApi } from "@/lib/api";
import type { SessionFile, SSEEventData, TokenUsageSummary, TokenUsageRecord } from "@/lib/api/types";
import {
  IconActivity,
  IconCoins,
  IconDownload,
  IconFilePreview,
  IconFileSearch,
} from "@/lib/icons";
import type { AttachmentFile, TaskObservationSummary } from "@/lib/session-events";
import { formatDuration, sessionFileToAttachment } from "@/lib/session-events";
import { formatFileSize } from "@/lib/utils";

export type SessionHeaderProps = {
  /** 此任务下的文件列表（用于「此任务中所有文件」弹窗） */
  files?: SessionFile[];
  /** 受控：文件列表弹窗是否打开（用于从页面其他处打开，如「查看此任务中所有的文件」） */
  fileListOpen?: boolean;
  /** 受控：文件列表弹窗打开状态变更 */
  onFileListOpenChange?: (open: boolean) => void;
  /** 当文件列表对话框打开时的回调，用于刷新文件列表 */
  onFetchFiles?: () => void | Promise<void>;
  /** 点击文件时的预览回调 */
  onFileClick?: (file: AttachmentFile) => void;
  /** 会话 ID，用于记忆按钮 */
  sessionId?: string;
  /** 记忆是否可编辑 */
  memoryEditable?: boolean;
  /** 会话 token 用量汇总 */
  tokenUsage?: TokenUsageSummary | null;
  /** 会话事件列表，用于调试面板 */
  events?: SSEEventData[];
  /** 是否已开启 debug 事件加载 */
  includeDebug?: boolean;
  /** 打开调试面板时触发 debug 事件订阅 */
  onDebugOpen?: () => void;
  /** 单任务观测摘要 */
  observationSummary?: TaskObservationSummary;
};

export const SessionHeader = memo(function SessionHeader({
  files,
  fileListOpen,
  onFileListOpenChange,
  onFetchFiles,
  onFileClick,
  sessionId,
  memoryEditable = true,
  tokenUsage,
  events = [],
  includeDebug = false,
  onDebugOpen,
  observationSummary,
}: SessionHeaderProps) {
  const t = useTranslations("sessionHeader");
  const tCommon = useTranslations("common");
  const [mounted, setMounted] = useState(false);
  const [internalOpen, setInternalOpen] = useState(false);
  const isControlled = fileListOpen !== undefined;
  const openState = isControlled ? fileListOpen : internalOpen;
  const setOpenState = useCallback(
    (v: boolean) => {
      if (isControlled) {
        onFileListOpenChange?.(v);
      } else {
        setInternalOpen(v);
      }
      // 当对话框打开时，触发文件列表刷新
      if (v && onFetchFiles) {
        onFetchFiles();
      }
    },
    [isControlled, onFileListOpenChange, onFetchFiles],
  );

  const fileList = Array.isArray(files) ? files : [];

  const uniqueFileList = useMemo(() => {
    const map = new Map<string, SessionFile>();
    for (const file of fileList) {
      const key = file.filepath || file.filename;
      map.set(key, file);
    }
    return Array.from(map.values());
  }, [fileList]);

  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [tokenDetailOpen, setTokenDetailOpen] = useState(false);
  const [tokenRecords, setTokenRecords] = useState<TokenUsageRecord[]>([]);
  const [tokenDetailLoading, setTokenDetailLoading] = useState(false);

  const handleOpenTokenDetail = useCallback(async () => {
    if (!sessionId) return;
    setTokenDetailOpen(true);
    setTokenDetailLoading(true);
    try {
      const data = await sessionApi.getTokenUsage(sessionId);
      setTokenRecords(data.records ?? []);
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("loadTokenDetailFailed");
      toast.error(msg);
      setTokenRecords([]);
    } finally {
      setTokenDetailLoading(false);
    }
  }, [sessionId, t]);

  const handleDownload = useCallback(
    async (file: SessionFile, e: React.MouseEvent) => {
      e.stopPropagation();
      if (downloadingId) return;
      setDownloadingId(file.id);
      try {
        const blob = await fileApi.downloadFile(file.id);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = file.filename || `file-${file.id}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        toast.success(t("downloadSuccess", { filename: file.filename }));
      } catch (err) {
        const msg = err instanceof Error ? err.message : t("downloadFailed");
        toast.error(t("downloadFailedWithName", { filename: file.filename, error: msg }));
      } finally {
        setDownloadingId(null);
      }
    },
    [downloadingId, t],
  );

  const handleFileItemClick = useCallback(
    (file: SessionFile) => {
      if (onFileClick) {
        onFileClick(sessionFileToAttachment(file));
        setOpenState(false);
      }
    },
    [onFileClick, setOpenState],
  );

  useEffect(() => {
    setMounted(true);
  }, []);

  return (
    <header className="bg-background/95 sticky top-0 z-10 flex flex-shrink-0 flex-row items-center justify-end gap-2 px-4 pt-2 pb-2">
      <div className="flex shrink-0 items-center gap-0.5">
        {observationSummary &&
          (observationSummary.toolCount > 0 || observationSummary.durationMs !== undefined) && (
            <div
              className="border-border/70 bg-card text-muted-foreground flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs shadow-[var(--shadow-card)]"
              title={t("observationTitle", {
                tools: observationSummary.toolCount,
                waits: observationSummary.waitCount,
              })}
            >
              <IconActivity className="size-3.5 shrink-0" />
              <span>{observationSummary.toolCount} tools</span>
              {observationSummary.durationMs !== undefined && (
                <span className="text-muted-foreground/70">
                  · {formatDuration(observationSummary.durationMs)}
                </span>
              )}
            </div>
          )}
        {tokenUsage && tokenUsage.total_tokens > 0 && (
          <button
            type="button"
            onClick={handleOpenTokenDetail}
            className="border-border/70 bg-card text-muted-foreground hover:bg-muted/70 flex cursor-pointer items-center gap-1 rounded-full border px-2.5 py-1 text-xs shadow-[var(--shadow-card)] transition-colors"
            title={t("tokenUsageTitle", {
              prompt: tokenUsage.prompt_tokens.toLocaleString(),
              completion: tokenUsage.completion_tokens.toLocaleString(),
              calls: tokenUsage.call_count,
            })}
          >
            <IconCoins className="size-3.5 shrink-0 text-amber-600" />
            <span>{tokenUsage.total_tokens.toLocaleString()} tok</span>
            {tokenUsage.estimated_cost_usd > 0 && (
              <span className="text-muted-foreground/70">
                · ${tokenUsage.estimated_cost_usd.toFixed(4)}
              </span>
            )}
          </button>
        )}
        {sessionId && (
          <SessionMemorySheet sessionId={sessionId} editable={memoryEditable} compact />
        )}
        <SessionDebugSheet
          events={events}
          includeDebug={includeDebug}
          compact
          onOpen={onDebugOpen}
        />
        {mounted ? (
          <Dialog open={openState} onOpenChange={setOpenState}>
            <DialogTrigger asChild>
              <Button variant="ghost" size="icon-sm" className="flex-shrink-0 cursor-pointer">
                <IconFileSearch />
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t("allFilesTitle")}</DialogTitle>
              </DialogHeader>
              <ScrollArea className="h-[500px]">
                <div className="flex flex-col gap-1">
                  {uniqueFileList.length === 0 ? (
                    <p className="text-muted-foreground py-4 text-sm">{t("noFiles")}</p>
                  ) : (
                    uniqueFileList.map((file) => (
                      <Item
                        key={file.id}
                        variant="default"
                        className="hover:bg-muted/60 flex-shrink-0 cursor-pointer gap-2 p-2"
                        onClick={() => handleFileItemClick(file)}
                      >
                        <ItemMedia>
                          <Avatar className="size-8">
                            <AvatarGroupCount>
                              <IconFilePreview />
                            </AvatarGroupCount>
                          </Avatar>
                        </ItemMedia>
                        <ItemContent className="gap-0">
                          <ItemTitle className="text-foreground text-sm">{file.filename}</ItemTitle>
                          <ItemDescription className="text-xs">
                            {file.extension.replace(/^\./, "")} · {formatFileSize(file.size)}
                          </ItemDescription>
                        </ItemContent>
                        <ItemActions>
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            className="cursor-pointer"
                            onClick={(e) => handleDownload(file, e)}
                            disabled={downloadingId === file.id}
                            aria-label={t("downloadFile", { filename: file.filename })}
                          >
                            <IconDownload />
                          </Button>
                        </ItemActions>
                      </Item>
                    ))
                  )}
                </div>
              </ScrollArea>
            </DialogContent>
          </Dialog>
        ) : (
          <Button variant="ghost" size="icon-sm" className="flex-shrink-0 cursor-pointer">
            <IconFileSearch />
          </Button>
        )}
        {sessionId && (
          <Dialog open={tokenDetailOpen} onOpenChange={setTokenDetailOpen}>
            <DialogContent className="max-w-2xl">
              <DialogHeader>
                <DialogTitle>{t("tokenDetailTitle")}</DialogTitle>
              </DialogHeader>
              <ScrollArea className="h-[420px]">
                {tokenDetailLoading ? (
                  <p className="text-muted-foreground py-4 text-sm">{tCommon("loading")}</p>
                ) : tokenRecords.length === 0 ? (
                  <p className="text-muted-foreground py-4 text-sm">{t("noCallRecords")}</p>
                ) : (
                  <div className="flex flex-col gap-2">
                    {tokenRecords.map((record) => (
                      <div
                        key={record.id}
                        className="border-border/60 bg-card rounded-lg border px-3 py-2 text-xs"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-medium">{record.agent || record.call_type}</span>
                          <span className="text-muted-foreground">{record.total_tokens} tok</span>
                        </div>
                        <div className="text-muted-foreground mt-1">
                          {record.model_name} · prompt {record.prompt_tokens} · completion{" "}
                          {record.completion_tokens}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </ScrollArea>
            </DialogContent>
          </Dialog>
        )}
      </div>
    </header>
  );
});
