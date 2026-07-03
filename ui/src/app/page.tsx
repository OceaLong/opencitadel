"use client";

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { ChatHeader } from "@/components/chat-header";
import { ChatInput, type ChatInputRef } from "@/components/chat-input";
import {
  ContextSelector,
  type SessionContextSelection,
} from "@/components/context-selector";
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
import { IconSecurity } from "@/lib/icons";
import { useRequireAuth } from "@/hooks/use-require-auth";

export default function Page() {
  const router = useRouter();
  const t = useTranslations("home");
  const { requireAuth } = useRequireAuth();
  const chatInputRef = useRef<ChatInputRef>(null);
  const [sending, setSending] = useState(false);
  const [modelId, setModelId] = useState<string | undefined>();
  const [skillId, setSkillId] = useState<string | undefined>();
  const [activeSkill, setActiveSkill] = useState<Skill | null>(null);
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [hasModels, setHasModels] = useState<boolean | null>(null);
  const [scopeDialogOpen, setScopeDialogOpen] = useState(false);
  const [context, setContext] = useState<SessionContextSelection>({});
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
      toast.error(t("noModel"));
      setSending(false);
      return;
    }

    setSending(true);

    try {
      const hasContext = Boolean(context.codebaseId || context.knowledgeBaseId);
      const session = await sessionApi.createSession({
        model_id: resolvedModelId,
        skill_id: skillId,
        thinking_enabled: thinkingEnabled,
        operator_scope: operatorScope,
        codebase_id: context.codebaseId,
        knowledge_base_id: context.knowledgeBaseId,
        mode: hasContext ? "ask" : undefined,
      });
      const sessionId = session.session_id;

      const attachments = files.map((file) => file.id);
      const payload = JSON.stringify({ message, attachments });
      const encoded = btoa(encodeURIComponent(payload));

      router.push(`/sessions/${sessionId}?init=${encoded}`);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : t("createFailed");
      toast.error(errorMessage);
      setSending(false);
      throw error;
    }
  };

  const handleSend = async (message: string, files: FileInfo[]) => {
    if (sending) return;
    if (!requireAuth(t("loginRequired"))) return;

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
              <IconSecurity className="text-primary hidden size-7 sm:inline" />
              {t("title")}
            </div>
            <div className="text-muted-foreground mt-1 text-base font-normal sm:text-lg">
              {t("subtitle")}
            </div>
          </div>
          <ChatInput
            ref={chatInputRef}
            className="mb-4 sm:mb-6"
            onSend={handleSend}
            disabled={sending}
            toolbarRight={
              <>
                <ContextSelector value={context} onChange={setContext} disabled={sending} />
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
          <div className="text-muted-foreground mb-4 flex flex-wrap gap-2 text-xs sm:mb-6">
            <Link href="/codebase" className="hover:text-foreground underline-offset-4 hover:underline">
              {t("manageCodebase")}
            </Link>
            <span>·</span>
            <Link href="/knowledge" className="hover:text-foreground underline-offset-4 hover:underline">
              {t("manageKnowledge")}
            </Link>
          </div>
          <SuggestedQuestions onQuestionClick={handleQuestionClick} />
        </div>
      </div>
    </div>
  );
}
