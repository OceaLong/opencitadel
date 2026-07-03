"use client";

import { use } from "react";

import { ChatHeader } from "@/components/chat-header";
import { KnowledgeDetailRedirect } from "@/components/knowledge/knowledge-detail-redirect";

export default function KnowledgeDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <div className="flex h-full flex-col">
      <ChatHeader />
      <KnowledgeDetailRedirect knowledgeBaseId={id} />
    </div>
  );
}
