"use client";

import { use } from "react";

import { CodebaseDetailRedirect } from "@/components/codebase/codebase-detail-redirect";

export default function CodebaseDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <div className="flex h-full flex-col">
      <CodebaseDetailRedirect codebaseId={id} />
    </div>
  );
}
