"use client";

import { ChatHeader } from "@/components/chat-header";
import { CodebaseWorkspace } from "@/components/codebase/codebase-workspace";

export default function CodebasePage() {
  return (
    <div className="flex h-full flex-col">
      <ChatHeader showSidebarTrigger={false} />
      <CodebaseWorkspace />
    </div>
  );
}
