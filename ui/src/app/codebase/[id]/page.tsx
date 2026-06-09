"use client";

import { use } from "react";

import { ChatHeader } from "@/components/chat-header";
import { CodebaseWorkspace } from "@/components/codebase/codebase-workspace";

export default function CodebaseDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <div className="flex h-full flex-col">
      <ChatHeader />
      <CodebaseWorkspace codebaseId={id} />
    </div>
  );
}
