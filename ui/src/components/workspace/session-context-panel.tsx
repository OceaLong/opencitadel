"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { KnowledgeContextPanel } from "@/components/workspace/knowledge-context-panel";
import { SessionResourceVersion } from "@/components/workspace/session-resource-version";

import type { SessionResourceBinding } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const EMPTY_RESOURCE_BINDINGS: SessionResourceBinding[] = [];

type SessionContextPanelProps = {
  knowledgeBaseId?: string | null;
  sessionId?: string;
  resourceBindings?: SessionResourceBinding[];
  kbSourceRef?: React.MutableRefObject<((value: string) => void) | null>;
  className?: string;
};

export function SessionContextPanel({
  knowledgeBaseId,
  sessionId,
  resourceBindings,
  kbSourceRef,
  className,
}: SessionContextPanelProps) {
  const t = useTranslations("workspaceContext");
  const suppliedBindings = resourceBindings ?? EMPTY_RESOURCE_BINDINGS;
  const [currentBindings, setCurrentBindings] = useState(suppliedBindings);
  useEffect(() => {
    setCurrentBindings(suppliedBindings);
  }, [suppliedBindings]);
  const hasKb = Boolean(knowledgeBaseId);
  const boundKnowledgeVersionId = currentBindings.find(
    (binding) =>
      binding.is_current &&
      binding.resource_kind === "knowledge_base" &&
      binding.resource_id === knowledgeBaseId,
  )?.version_id;
  if (!hasKb) return null;

  const knowledgePanel = boundKnowledgeVersionId ? (
    <KnowledgeContextPanel
      knowledgeBaseId={knowledgeBaseId!}
      versionId={boundKnowledgeVersionId}
      onSourceClickRef={kbSourceRef}
    />
  ) : (
    <p role="alert" className="text-muted-foreground p-4 text-sm">
      {t("knowledgeVersionUnavailable")}
    </p>
  );

  return (
    <div className={cn("border-border flex h-full w-96 shrink-0 flex-col border-l", className)}>
      {sessionId && (
        <SessionResourceVersion
          sessionId={sessionId}
          bindings={currentBindings}
          onBindingsChanged={setCurrentBindings}
        />
      )}
      {knowledgePanel}
    </div>
  );
}

export function useSessionContextRefs() {
  const kbSourceRef = useRef<((value: string) => void) | null>(null);

  const handleTimelineSourceClick = (path: string) => {
    kbSourceRef.current?.(path);
  };

  return { kbSourceRef, handleTimelineSourceClick };
}
