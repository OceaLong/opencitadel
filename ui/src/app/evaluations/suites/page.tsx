"use client";
import { EvaluationBoundary } from "@/components/evaluation/evaluation-boundary";
import { EvaluationList } from "@/components/evaluation/evaluation-list";
export default function Page() {
  return (
    <EvaluationBoundary>
      {(access) => <EvaluationList kind="suites" access={access} />}
    </EvaluationBoundary>
  );
}
