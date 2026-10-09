"use client";
import { EvaluationBoundary } from "@/components/evaluation/evaluation-boundary";
import { ReviewQueue } from "@/components/evaluation/review-queue";
export default function Page() {
  return <EvaluationBoundary>{(access) => <ReviewQueue access={access} />}</EvaluationBoundary>;
}
