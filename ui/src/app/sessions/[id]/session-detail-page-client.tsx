"use client";

import { useMemo } from "react";
import { useSearchParams } from "next/navigation";

import { SessionDetailView } from "@/components/session/session-detail-view";

type InitData = {
  initialMessage?: string;
  initialAttachments?: string[];
  hasInitialMessage: boolean;
};

/**
 * 任务详情页客户端视图：展示会话标题、事件时间线、任务进度与输入框。
 * - sessionId 由服务端薄壳从路由参数解析后传入
 * - 支持从 URL 参数读取初始消息（用于首页跳转场景）
 */
export function SessionDetailPageClient({ sessionId }: { sessionId: string }) {
  const searchParams = useSearchParams();

  const initData = useMemo<InitData>(() => {
    // 尝试从 URL 参数读取初始消息（Base64 编码）
    const initParam = searchParams.get("init");
    if (!initParam) {
      return { hasInitialMessage: false };
    }
    try {
      // 解码 Base64
      const decoded = decodeURIComponent(atob(initParam));
      const { message, attachments } = JSON.parse(decoded);
      return {
        initialMessage: message,
        initialAttachments: attachments,
        hasInitialMessage: true,
      };
    } catch {
      return { hasInitialMessage: false };
    }
  }, [searchParams]);

  return (
    <SessionDetailView
      sessionId={sessionId}
      initialMessage={initData.initialMessage}
      initialAttachments={initData.initialAttachments}
      hasInitialMessage={initData.hasInitialMessage}
    />
  );
}
