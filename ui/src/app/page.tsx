"use client";

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Code2, Shield } from "lucide-react";
import { toast } from "sonner";

import { ChatHeader } from "@/components/chat-header";
import { ChatInput, type ChatInputRef } from "@/components/chat-input";
import {
  OperatorScopeDialog,
  type OperatorScope,
} from "@/components/operator-scope-dialog";
import { SessionModelPicker } from "@/components/session-model-picker";
import { SessionSkillPicker } from "@/components/session-skill-picker";
import { SuggestedQuestions } from "@/components/suggested-questions";
import { ThinkingToggle } from "@/components/thinking-toggle";

import { invalidateModelsCache, loadModels, resolveDefaultModelId } from "@/lib/api/models-cache";
import { sessionApi } from "@/lib/api/session";
import type { FileInfo, Skill } from "@/lib/api/types";
import { useRequireAuth } from "@/hooks/use-require-auth";

export default function Page() {
  const router = useRouter();
  const { requireAuth } = useRequireAuth();
  const chatInputRef = useRef<ChatInputRef>(null);
  const [sending, setSending] = useState(false);
  const [modelId, setModelId] = useState<string | undefined>();
  const [skillId, setSkillId] = useState<string | undefined>();
  const [activeSkill, setActiveSkill] = useState<Skill | null>(null);
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [hasModels, setHasModels] = useState<boolean | null>(null);
  const [scopeDialogOpen, setScopeDialogOpen] = useState(false);
  const pendingSendRef = useRef<{ message: string; files: FileInfo[] } | null>(null);

  const handleDefaultModelLoaded = useCallback((id: string | undefined) => {
    setModelId((current) => current ?? id);
  }, []);

  const handleModelsResolved = useCallback((resolved: boolean) => {
    setHasModels(resolved);
  }, []);

  const handleQuestionClick = (question: string) => {
    chatInputRef.current?.setInputText(question);
  };

  const createSessionAndNavigate = async (
    message: string,
    files: FileInfo[],
    operatorScope?: OperatorScope,
  ) => {
    let resolvedModelId = modelId;

    if (!resolvedModelId) {
      invalidateModelsCache();
      try {
        const models = await loadModels();
        resolvedModelId = resolveDefaultModelId(models);
        if (resolvedModelId) {
          setModelId(resolvedModelId);
        }
      } catch {
        resolvedModelId = undefined;
      }
    }

    if (hasModels === false || !resolvedModelId) {
      toast.error("请先在设置中添加模型");
      setSending(false);
      return;
    }

    setSending(true);

    try {
      const session = await sessionApi.createSession({
        model_id: resolvedModelId,
        skill_id: skillId,
        thinking_enabled: thinkingEnabled,
        operator_scope: operatorScope,
      });
      const sessionId = session.session_id;

      const attachments = files.map((file) => file.id);
      const payload = JSON.stringify({ message, attachments });
      const encoded = btoa(encodeURIComponent(payload));

      router.push(`/sessions/${sessionId}?init=${encoded}`);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "创建会话失败";
      toast.error(errorMessage);
      setSending(false);
      throw error;
    }
  };

  const handleSend = async (message: string, files: FileInfo[]) => {
    if (sending) return;
    if (!requireAuth("登录后即可开始 AI 对话")) return;

    if (activeSkill?.slug === "web-operator") {
      pendingSendRef.current = { message, files };
      setScopeDialogOpen(true);
      return;
    }

    await createSessionAndNavigate(message, files);
  };

  return (
    <div className="flex h-full flex-col">
      <ChatHeader />
      <OperatorScopeDialog
        open={scopeDialogOpen}
        onOpenChange={setScopeDialogOpen}
        onConfirm={(scope) => {
          const pending = pendingSendRef.current;
          if (pending) {
            void createSessionAndNavigate(pending.message, pending.files, scope);
            pendingSendRef.current = null;
          }
        }}
      />
      <div className="-mt-12 flex flex-1 items-center justify-center px-4 py-6 sm:-mt-16 sm:py-8">
        <div className="mx-auto w-full max-w-full sm:max-w-[768px] sm:min-w-[390px]">
          <div className="mb-4 text-center text-[24px] font-bold tracking-tight sm:mb-6 sm:text-left sm:text-[32px]">
            <div className="text-foreground flex items-center justify-center gap-2 sm:justify-start">
              <Shield className="text-primary hidden size-7 sm:inline" />
              监管级自主执行 Agent
            </div>
            <div className="text-muted-foreground mt-1 text-base font-normal sm:text-lg">
              规划 · 审批 · VNC 接管 · 检查点回滚 · 全工具审计
            </div>
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
                  onModelsResolved={handleModelsResolved}
                  disabled={sending}
                />
                <SessionSkillPicker
                  value={skillId}
                  onChange={(id) => setSkillId(id)}
                  onSkillLoaded={setActiveSkill}
                  disabled={sending}
                />
              </>
            }
          />
          <Link
            href="/codebase"
            className="border-border bg-card hover:bg-muted/60 mb-4 flex items-center gap-3 rounded-xl border p-4 shadow-[var(--shadow-card)] transition-colors sm:mb-6"
          >
            <div className="bg-primary/10 flex size-10 items-center justify-center rounded-lg">
              <Code2 className="text-primary size-5" />
            </div>
            <div className="text-left">
              <div className="text-sm font-semibold">代码知识库</div>
              <div className="text-muted-foreground text-xs">
                上传代码库，生成架构图与调用链，Ask 问答 / Agent 改码
              </div>
            </div>
          </Link>
          <SuggestedQuestions onQuestionClick={handleQuestionClick} />
        </div>
      </div>
    </div>
  );
}
