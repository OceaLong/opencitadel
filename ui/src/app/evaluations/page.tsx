"use client";
import { BatchList } from "@/components/evaluation/batch-list";
import { EvaluationBoundary } from "@/components/evaluation/evaluation-boundary";
import { EvaluationHome } from "@/components/evaluation/evaluation-list";
export default function Page() {
  return (
    <EvaluationBoundary>
      {(access) => (
        <>
          <EvaluationHome />
          <BatchList access={access} />
        </>
      )}
    </EvaluationBoundary>
  );
}
