"use client";
import { use } from "react";

import { BatchOverview } from "@/components/evaluation/batch-overview";
import { EvaluationBoundary } from "@/components/evaluation/evaluation-boundary";
export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <EvaluationBoundary>{(access) => <BatchOverview id={id} access={access} />}</EvaluationBoundary>
  );
}
