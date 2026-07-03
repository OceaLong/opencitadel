"use client";

import { ChatHeader } from "@/components/chat-header";
import { KnowledgeLibrary } from "@/components/knowledge/knowledge-library";

export default function KnowledgePage() {
  return (
    <div className="flex h-full flex-col">
      <ChatHeader />
      <KnowledgeLibrary />
    </div>
  );
}
