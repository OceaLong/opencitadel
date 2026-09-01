"use client";

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { ContextSelector, type SessionContextSelection } from "@/components/context-selector";
import { ChatInput, type ChatInputRef } from "@/components/session/chat-input";
import {
  OperatorScopeDialog,
  type OperatorSessionConfig,
} from "@/components/session/operator-scope-dialog";
import { SuggestedQuestions } from "@/components/session/suggested-questions";
import { ThinkingToggle } from "@/components/session/thinking-toggle";
import { SessionModelPicker } from "@/components/session-model-picker";
import { SessionSkillPicker } from "@/components/session-skill-picker";

import { useRequireAuth } from "@/hooks/use-require-auth";
import { boundModelId, loadInferenceSnapshot } from "@/lib/api/inference-cache";
import { sessionApi } from "@/lib/api/session";
import type { FileInfo, Skill } from "@/lib/api/types";
import { useClientDataScope } from "@/providers/client-data-provider";

export default function Page() {
  const router = useRouter();
  const t = useTranslations("home");
  const { requireAuth } = useRequireAuth();
  const { loadResource } = useClientDataScope();
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

  const handleBoundModelLoaded = useCallback((id: string | undefined) => {
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
    operatorConfig?: OperatorSessionConfig,
  ) => {
    let resolvedModelId = modelId;

    if (!resolvedModelId) {
      try {
        const inference = await loadResource("inference", loadInferenceSnapshot);
        resolvedModelId = boundModelId(inference.bindings, "chat");
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
      const hasContext = Boolean(context.knowledgeBaseId);
      const session = await sessionApi.createSession({
        model_id: resolvedModelId,
        skill_id: skillId,
        thinking_enabled: thinkingEnabled,
        operator_scope: operatorConfig?.scope,
        operator_domains: operatorConfig?.operatorDomains,
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
      <OperatorScopeDialog
        open={scopeDialogOpen}
        onOpenChange={setScopeDialogOpen}
        onConfirm={(config) => {
          const pending = pendingSendRef.current;
          if (pending) {
            void createSessionAndNavigate(pending.message, pending.files, config);
            pendingSendRef.current = null;
          }
        }}
      />
      <div className="flex flex-1 items-center justify-center px-4 py-6 sm:py-8">
        <div className="sm:max-w-content mx-auto w-full max-w-full sm:min-w-[390px]">
          <div className="text-foreground mb-6 text-center text-2xl font-semibold tracking-tight sm:mb-8 sm:text-left sm:text-3xl">
            {t("title")}
            <div className="text-muted-foreground mt-2 text-sm font-normal sm:text-base">
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
                  onBoundModelLoaded={handleBoundModelLoaded}
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
          <div className="space-y-3">
            <nav aria-label={t("secondaryNav")} className="flex flex-wrap gap-4">
              <Link
                href="/knowledge"
                className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/40 rounded-sm text-xs underline-offset-4 hover:underline focus-visible:ring-2"
              >
                {t("manageKnowledge")}
              </Link>
            </nav>
            <SuggestedQuestions onQuestionClick={handleQuestionClick} />
          </div>
        </div>
      </div>
    </div>
  );
}
