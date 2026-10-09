"use client";
import { use } from "react";

import { EvaluationBoundary } from "@/components/evaluation/evaluation-boundary";
import { VersionEditor } from "@/components/evaluation/version-editor";
export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <EvaluationBoundary>
      {(access) => <VersionEditor key={id} id={id} kind="configs" access={access} />}
    </EvaluationBoundary>
  );
}
