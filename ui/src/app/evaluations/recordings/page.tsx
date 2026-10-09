"use client";
import { EvaluationBoundary } from "@/components/evaluation/evaluation-boundary";
import { RecordingManager } from "@/components/evaluation/recording-manager";
export default function Page() {
  return (
    <EvaluationBoundary>{(access) => <RecordingManager access={access} />}</EvaluationBoundary>
  );
}
