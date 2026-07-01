"use client";

import { ChatHeader } from "@/components/chat-header";
import { KnowledgeWorkspace } from "@/components/knowledge/knowledge-workspace";

export default function KnowledgePage() {
  return (
    <div className="flex h-full flex-col">
      <ChatHeader showSidebarTrigger={false} />
      <KnowledgeWorkspace />
    </div>
  );
}
