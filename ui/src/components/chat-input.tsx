"use client";

import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import { ArrowUp, FileText, Loader2, Paperclip, Pause, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Avatar, AvatarGroupCount } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
import { ScrollArea } from "@/components/ui/scroll-area";

import { fileApi } from "@/lib/api/file";
import type { FileInfo } from "@/lib/api/types";
import { cn, formatFileSize } from "@/lib/utils";

type ChatInputProps = {
  className?: string;
  onInputValueChange?: (value: string) => void;
  onSend?: (message: string, files: FileInfo[]) => Promise<void>;
  disabled?: boolean;
  /** 当前会话 ID，上传附件时会关联到该会话 */
  sessionId?: string | null;
  /** 任务是否正在运行中 */
  isRunning?: boolean;
  /** 点击暂停按钮的回调 */
  onStop?: () => void;
  /** 输入框底部右侧、发送按钮旁的自定义控件（模型/Skill 选择等） */
  toolbarRight?: React.ReactNode;
};

export type ChatInputRef = {
  setInputText: (text: string) => void;
  getInputValue: () => string;
  getFiles: () => FileInfo[];
};

export const ChatInput = forwardRef<ChatInputRef, ChatInputProps>(
  (
    {
      className,
      onInputValueChange,
      onSend,
      disabled = false,
      sessionId,
      isRunning = false,
      onStop,
      toolbarRight,
    },
    ref,
  ) => {
    const t = useTranslations("chatInput");
    const tCommon = useTranslations("common");
    const [files, setFiles] = useState<FileInfo[]>([]);
    const [uploading, setUploading] = useState(false);
    const [sending, setSending] = useState(false);
    const [inputValue, setInputValue] = useState("");
    const fileInputRef = useRef<HTMLInputElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const value = e.target.value;
      setInputValue(value);
      onInputValueChange?.(value);
    };

    useImperativeHandle(ref, () => ({
      setInputText: (text: string) => {
        setInputValue(text);
        onInputValueChange?.(text);
        // 聚焦到输入框
        textareaRef.current?.focus();
      },
      getInputValue: () => inputValue,
      getFiles: () => files,
    }));

    const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
      const selectedFiles = event.target.files;
      if (!selectedFiles || selectedFiles.length === 0) {
        return;
      }

      setUploading(true);

      try {
        const uploadPromises = Array.from(selectedFiles).map(async (file) => {
          try {
            const fileInfo = await fileApi.uploadFile({
              file,
              ...(sessionId && { session_id: sessionId }),
            });
            return fileInfo;
          } catch (error) {
            const errorMessage = error instanceof Error ? error.message : tCommon("uploadFailed");
            toast.error(t("fileUploadFailed", { name: file.name, error: errorMessage }));
            return null;
          }
        });

        const uploadedFiles = (await Promise.all(uploadPromises)).filter(
          (file): file is FileInfo => file !== null,
        );

        if (uploadedFiles.length > 0) {
          setFiles((prev) => [...prev, ...uploadedFiles]);
          toast.success(t("uploadSuccess", { count: uploadedFiles.length }));
        }
      } catch {
        toast.error(t("uploadError"));
      } finally {
        setUploading(false);
        // 重置input，以便可以重复选择同一文件
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
      }
    };

    const handleUploadClick = () => {
      fileInputRef.current?.click();
    };

    const handleRemoveFile = (fileId: string) => {
      setFiles((prev) => prev.filter((file) => file.id !== fileId));
    };

    const handleSend = async () => {
      const trimmedMessage = inputValue.trim();

      // 验证消息不为空
      if (!trimmedMessage) {
        toast.error(t("emptyMessage"));
        textareaRef.current?.focus();
        return;
      }

      // 如果提供了 onSend 回调，使用它
      if (onSend) {
        setSending(true);
        try {
          await onSend(trimmedMessage, files);
          // 发送成功后清空输入框和文件列表
          setInputValue("");
          setFiles([]);
          onInputValueChange?.("");
        } catch {
          // 错误处理由 onSend 内部处理
        } finally {
          setSending(false);
        }
      }
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // 支持 Ctrl/Cmd + Enter 发送
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        handleSend();
      }
    };

    return (
      <div
        className={cn(
          "bg-card border-border/70 flex w-full flex-col rounded-2xl border py-3 shadow-[var(--shadow-card)]",
          className,
        )}
      >
        {/* 顶部的文件列表 */}
        {files.length > 0 && (
          <div className="mb-1 w-full px-4">
            <ScrollArea className="w-full whitespace-nowrap">
              <div className="flex w-max space-x-4 pb-4">
                {files.map((file) => (
                  <Item key={file.id} variant="muted" className="flex-shrink-0 gap-2 p-2">
                    {/* 左侧文件图标 */}
                    <ItemMedia>
                      <Avatar className="size-8">
                        <AvatarGroupCount>
                          <FileText />
                        </AvatarGroupCount>
                      </Avatar>
                    </ItemMedia>
                    {/* 文件信息 */}
                    <ItemContent className="gap-0">
                      <ItemTitle className="text-foreground text-sm">{file.filename}</ItemTitle>
                      <ItemDescription className="text-xs">
                        {file.extension} · {formatFileSize(file.size)}
                      </ItemDescription>
                    </ItemContent>
                    <ItemActions>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="cursor-pointer"
                        onClick={() => handleRemoveFile(file.id)}
                        disabled={uploading}
                      >
                        <XCircle />
                      </Button>
                    </ItemActions>
                  </Item>
                ))}
              </div>
            </ScrollArea>
          </div>
        )}
        {/* 中间输入框 */}
        <div className="mb-3 px-4">
          <textarea
            ref={textareaRef}
            rows={2}
            value={inputValue}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            placeholder={t("placeholder")}
            className="scrollbar-hide text-foreground placeholder:text-muted-foreground h-[46px] min-h-[40px] w-full resize-none bg-transparent text-sm outline-none"
            disabled={sending || disabled}
          />
        </div>
        {/* 底部：左侧附件，右侧模型/Skill + 发送 */}
        <footer className="flex w-full flex-row items-center justify-between gap-2 px-3">
          <div className="flex shrink-0 items-center">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={handleFileSelect}
              disabled={uploading}
            />
            <Button
              variant="outline"
              className="h-8 w-8 shrink-0 cursor-pointer rounded-full"
              onClick={handleUploadClick}
              disabled={uploading}
            >
              {uploading ? <Loader2 className="size-4 animate-spin" /> : <Paperclip />}
            </Button>
          </div>
          <div className="flex min-w-0 shrink-0 items-center gap-1">
            {toolbarRight && (
              <div className="flex min-w-0 items-center gap-0.5 overflow-hidden">
                {toolbarRight}
              </div>
            )}
            {isRunning ? (
              <Button
                variant="outline"
                className="h-8 w-8 shrink-0 cursor-pointer rounded-full"
                onClick={onStop}
                disabled={!onStop}
              >
                <Pause className="size-4" />
              </Button>
            ) : (
              <Button
                variant="outline"
                className="h-8 w-8 shrink-0 cursor-pointer rounded-full"
                onClick={handleSend}
                disabled={sending || disabled || !inputValue.trim()}
              >
                {sending ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp />}
              </Button>
            )}
          </div>
        </footer>
      </div>
    );
  },
);

ChatInput.displayName = "ChatInput";
