"use client";

import { use } from "react";

import { ChatHeader } from "@/components/chat-header";
import { KnowledgeWorkspace } from "@/components/knowledge/knowledge-workspace";

export default function KnowledgeDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <div className="flex h-full flex-col">
      <ChatHeader showSidebarTrigger={false} />
      <KnowledgeWorkspace knowledgeBaseId={id} />
    </div>
  );
}
