"use client";
import { EnvironmentManager } from "@/components/evaluation/environment-manager";
import { EvaluationBoundary } from "@/components/evaluation/evaluation-boundary";
export default function Page() {
  return (
    <EvaluationBoundary>{(access) => <EnvironmentManager access={access} />}</EvaluationBoundary>
  );
}
