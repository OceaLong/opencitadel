"use client";

import { useCallback } from "react";
import { AlertCircle, CircuitBoard, Loader2, MoreHorizontal, Trash } from "lucide-react";

import { Avatar, AvatarGroupCount } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia } from "@/components/ui/item";

import type { Session } from "@/lib/api";
import { cn, formatRelativeDate } from "@/lib/utils";

type SessionItemProps = {
  session: Session;
  isActive: boolean;
  onClick: (sessionId: string) => void;
  onDelete: (session: Session) => void;
};

/**
 * 单个会话列表项
 * 展示会话标题、描述、时间及操作菜单
 */
export function SessionItem({ session, isActive, onClick, onDelete }: SessionItemProps) {
  const handleClick = useCallback(() => {
    onClick(session.session_id);
  }, [onClick, session.session_id]);

  const handleDelete = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onDelete(session);
    },
    [onDelete, session],
  );

  const description = session.latest_message || "暂无消息";
  const dateLabel = formatRelativeDate(session.latest_message_at);
  const isRunning = session.status === "running" || session.status === "waiting";
  const isFailed = session.status === "failed";

  return (
    <Item
      className={cn(
        "hover:bg-muted/70 cursor-pointer items-start gap-2 rounded-xl p-2 transition-colors",
        isActive && "bg-card shadow-[var(--shadow-card)]",
      )}
      onClick={handleClick}
    >
      {/* 左侧图标 */}
      <ItemMedia>
        <Avatar className="size-8">
          <AvatarGroupCount>
            {isRunning ? (
              <Loader2 className="animate-spin" />
            ) : isFailed ? (
              <AlertCircle className="text-destructive" />
            ) : (
              <CircuitBoard />
            )}
          </AvatarGroupCount>
        </Avatar>
      </ItemMedia>
      {/* 中间内容 */}
      <ItemContent className="min-w-0 gap-0">
        <p className="truncate text-sm font-medium">{session.title || "新任务"}</p>
        <p className="text-muted-foreground truncate text-xs">{description}</p>
      </ItemContent>
      {/* 右侧操作区 */}
      <ItemActions className="flex flex-col gap-0 self-start pt-0.5">
        {session.unread_message_count > 0 && (
          <span className="bg-primary text-primary-foreground mb-0.5 inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-[10px] font-medium">
            {session.unread_message_count > 99 ? "99+" : session.unread_message_count}
          </span>
        )}
        <ItemDescription className="text-xs whitespace-nowrap">{dateLabel}</ItemDescription>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="icon-xs"
              variant="ghost"
              className="cursor-pointer"
              onClick={(e) => e.stopPropagation()}
            >
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="center" side="bottom">
            <DropdownMenuItem
              variant="destructive"
              className="cursor-pointer"
              onClick={handleDelete}
            >
              <Trash />
              删除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </ItemActions>
    </Item>
  );
}
