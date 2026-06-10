"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, Coins, Download, FileSearchCorner, FileText } from "lucide-react";
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
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";

import { fileApi, sessionApi } from "@/lib/api";
import type { SessionFile, SSEEventData, TokenUsageSummary, TokenUsageRecord } from "@/lib/api/types";
import type { AttachmentFile, TaskObservationSummary } from "@/lib/session-events";
import { formatDuration, sessionFileToAttachment } from "@/lib/session-events";
import { formatFileSize } from "@/lib/utils";

export type SessionHeaderProps = {
  /** 任务/会话标题 */
  title?: string;
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
  /** 打开调试面板时触发 debug 事件订阅 */
  onDebugOpen?: () => void;
  /** 单任务观测摘要 */
  observationSummary?: TaskObservationSummary;
};

export function SessionHeader({
  title = "",
  files,
  fileListOpen,
  onFileListOpenChange,
  onFetchFiles,
  onFileClick,
  sessionId,
  memoryEditable = true,
  tokenUsage,
  events = [],
  onDebugOpen,
  observationSummary,
}: SessionHeaderProps) {
  const { open, isMobile } = useSidebar();
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

  // 对相同 filepath 的文件进行去重，保留最新的（数组中最后一个）
  const uniqueFileList = fileList.reduce((acc, file) => {
    // 使用 filepath 作为去重的 key，如果为空则使用 filename
    const key = file.filepath || file.filename;
    const existingIndex = acc.findIndex((f) => (f.filepath || f.filename) === key);

    if (existingIndex >= 0) {
      // 如果已存在，替换为当前文件（保留最新的）
      acc[existingIndex] = file;
    } else {
      // 如果不存在，添加到结果中
      acc.push(file);
    }

    return acc;
  }, [] as SessionFile[]);

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
      const msg = err instanceof Error ? err.message : "加载 Token 明细失败";
      toast.error(msg);
      setTokenRecords([]);
    } finally {
      setTokenDetailLoading(false);
    }
  }, [sessionId]);

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
        toast.success(`已下载「${file.filename}」`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : "下载失败";
        toast.error(`下载「${file.filename}」失败: ${msg}`);
      } finally {
        setDownloadingId(null);
      }
    },
    [downloadingId],
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
    <header className="bg-background/95 sticky top-0 z-10 flex flex-shrink-0 flex-row items-center justify-between gap-2 pt-3 pb-2">
      {(!open || isMobile) && <SidebarTrigger className="flex-shrink-0 cursor-pointer" />}
      <div className="text-foreground min-w-0 flex-1 overflow-hidden text-lg font-medium text-ellipsis whitespace-nowrap">
        {title || "未命名任务"}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        {observationSummary &&
          (observationSummary.toolCount > 0 || observationSummary.errorCount > 0) && (
            <div
              className="border-border/70 bg-card text-muted-foreground flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs shadow-[var(--shadow-card)]"
              title={`工具: ${observationSummary.toolCount} · 错误: ${observationSummary.errorCount} · 等待: ${observationSummary.waitCount}`}
            >
              <Activity className="size-3.5 shrink-0" />
              <span>{observationSummary.toolCount} tools</span>
              {observationSummary.durationMs !== undefined && (
                <span className="text-muted-foreground/70">
                  · {formatDuration(observationSummary.durationMs)}
                </span>
              )}
              {observationSummary.errorCount > 0 && (
                <span className="text-red-600">· {observationSummary.errorCount} err</span>
              )}
            </div>
          )}
        {tokenUsage && tokenUsage.total_tokens > 0 && (
          <button
            type="button"
            onClick={handleOpenTokenDetail}
            className="border-border/70 bg-card text-muted-foreground hover:bg-muted/70 flex cursor-pointer items-center gap-1 rounded-full border px-2.5 py-1 text-xs shadow-[var(--shadow-card)] transition-colors"
            title={`Prompt: ${tokenUsage.prompt_tokens.toLocaleString()} · Completion: ${tokenUsage.completion_tokens.toLocaleString()} · Calls: ${tokenUsage.call_count} · 点击查看明细`}
          >
            <Coins className="size-3.5 shrink-0 text-amber-600" />
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
        <SessionDebugSheet events={events} compact onOpen={onDebugOpen} />
        {mounted ? (
          <Dialog open={openState} onOpenChange={setOpenState}>
            <DialogTrigger asChild>
              <Button variant="ghost" size="icon-sm" className="flex-shrink-0 cursor-pointer">
                <FileSearchCorner />
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>此任务中的所有文件</DialogTitle>
              </DialogHeader>
              <ScrollArea className="h-[500px]">
                <div className="flex flex-col gap-1">
                  {uniqueFileList.length === 0 ? (
                    <p className="text-muted-foreground py-4 text-sm">暂无文件</p>
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
                              <FileText />
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
                            aria-label={`下载 ${file.filename}`}
                          >
                            <Download />
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
            <FileSearchCorner />
          </Button>
        )}
        {sessionId && (
          <Dialog open={tokenDetailOpen} onOpenChange={setTokenDetailOpen}>
            <DialogContent className="max-w-2xl">
              <DialogHeader>
                <DialogTitle>Token 用量明细</DialogTitle>
              </DialogHeader>
              <ScrollArea className="h-[420px]">
                {tokenDetailLoading ? (
                  <p className="text-muted-foreground py-4 text-sm">加载中...</p>
                ) : tokenRecords.length === 0 ? (
                  <p className="text-muted-foreground py-4 text-sm">暂无调用记录</p>
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
}
