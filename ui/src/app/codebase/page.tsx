"use client";

import { ChatHeader } from "@/components/chat-header";
import { CodebaseLibrary } from "@/components/codebase/codebase-library";

export default function CodebasePage() {
  return (
    <div className="flex h-full flex-col">
      <ChatHeader />
      <CodebaseLibrary />
    </div>
  );
}
