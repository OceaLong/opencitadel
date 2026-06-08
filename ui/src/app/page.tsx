"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { ChatHeader } from "@/components/chat-header";
import { ChatInput, type ChatInputRef } from "@/components/chat-input";
import { SessionModelPicker } from "@/components/session-model-picker";
import { SessionSkillPicker } from "@/components/session-skill-picker";
import { SuggestedQuestions } from "@/components/suggested-questions";
import { ThinkingToggle } from "@/components/thinking-toggle";

import { sessionApi } from "@/lib/api/session";
import type { FileInfo } from "@/lib/api/types";

export default function Page() {
  const router = useRouter();
  const chatInputRef = useRef<ChatInputRef>(null);
  const [sending, setSending] = useState(false);
  const [modelId, setModelId] = useState<string | undefined>();
  const [skillId, setSkillId] = useState<string | undefined>();
  const [thinkingEnabled, setThinkingEnabled] = useState(false);

  const handleDefaultModelLoaded = useCallback((id: string | undefined) => {
    setModelId((current) => current ?? id);
  }, []);

  const handleQuestionClick = (question: string) => {
    chatInputRef.current?.setInputText(question);
  };

  const handleSend = async (message: string, files: FileInfo[]) => {
    if (sending) return;

    setSending(true);

    try {
      // 1. 创建新会话
      const session = await sessionApi.createSession({
        model_id: modelId,
        skill_id: skillId,
        thinking_enabled: thinkingEnabled,
      });
      const sessionId = session.session_id;

      // 2. 将消息数据编码到 URL，在详情页发送
      const attachments = files.map((file) => file.id);
      const payload = JSON.stringify({ message, attachments });
      // 使用 Base64 编码避免 URL 特殊字符问题
      const encoded = btoa(encodeURIComponent(payload));

      // 3. 跳转到详情页，携带编码后的初始消息
      router.push(`/sessions/${sessionId}?init=${encoded}`);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "创建会话失败";
      toast.error(errorMessage);
      setSending(false);
      throw error;
    }
  };

  return (
    <div className="flex h-full flex-col">
      {/* 顶部header */}
      <ChatHeader />
      {/* 中间对话框 - 垂直居中，视觉上移一个导航栏高度 */}
      <div className="-mt-12 flex flex-1 items-center justify-center px-4 py-6 sm:-mt-16 sm:py-8">
        <div className="mx-auto w-full max-w-full sm:max-w-[768px] sm:min-w-[390px]">
          {/* 对话提示内容 */}
          <div className="mb-4 text-center text-[24px] font-bold tracking-tight sm:mb-6 sm:text-left sm:text-[32px]">
            <div className="text-foreground">你好，同学</div>
            <div className="text-muted-foreground">我能为你做什么?</div>
          </div>
          <ChatInput
            ref={chatInputRef}
            className="mb-4 sm:mb-6"
            onSend={handleSend}
            disabled={sending}
            toolbarRight={
              <>
                <ThinkingToggle
                  enabled={thinkingEnabled}
                  onChange={setThinkingEnabled}
                  disabled={sending}
                />
                <SessionModelPicker
                  value={modelId}
                  onChange={setModelId}
                  onDefaultModelLoaded={handleDefaultModelLoaded}
                  disabled={sending}
                />
                <SessionSkillPicker
                  value={skillId}
                  onChange={(id) => setSkillId(id)}
                  disabled={sending}
                />
              </>
            }
          />
          {/* 推荐对话内容 */}
          <SuggestedQuestions onQuestionClick={handleQuestionClick} />
        </div>
      </div>
    </div>
  );
}
