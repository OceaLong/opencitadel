import type { CloseType, QuizBank, QuizQuestion, QuizResult } from "./types";
import { isChoiceQuestion, isLikertQuestion } from "./types";

export function scoreLikertValue(
  value: number,
  agreeDim: string,
  disagreeDim: string,
  scale: number = 5,
): Record<string, number> {
  const mid = (scale + 1) / 2;
  const delta = value - mid;
  const scores: Record<string, number> = {};
  if (delta > 0) scores[agreeDim] = delta;
  else if (delta < 0) scores[disagreeDim] = -delta;
  return scores;
}

export function scoreAnswers(
  bank: QuizBank,
  answers: Record<string, number | string>,
): Record<string, number> {
  const scores: Record<string, number> = {};

  for (const q of bank.questions) {
    const raw = answers[q.id];
    if (raw === undefined || raw === "") continue;

    if (isLikertQuestion(q)) {
      const value = typeof raw === "number" ? raw : parseInt(String(raw), 10);
      if (Number.isNaN(value)) continue;
      const scale = q.scale ?? 5;
      const partial = scoreLikertValue(value, q.agreeDim, q.disagreeDim, scale);
      for (const [dim, w] of Object.entries(partial)) {
        scores[dim] = (scores[dim] ?? 0) + w;
      }
      continue;
    }

    if (isChoiceQuestion(q)) {
      const optId = String(raw);
      const opt = q.options.find((o) => o.id === optId);
      if (!opt) continue;
      for (const [dim, w] of Object.entries(opt.weights)) {
        scores[dim] = (scores[dim] ?? 0) + w;
      }
    }
  }

  return scores;
}

export function toPercentages(scores: Record<string, number>): Record<string, number> {
  const positive = Object.fromEntries(
    Object.entries(scores).map(([k, v]) => [k, Math.max(0, v)]),
  );
  const total = Object.values(positive).reduce((a, b) => a + b, 0) || 1;
  return Object.fromEntries(
    Object.entries(positive).map(([k, v]) => [k, Math.round((v / total) * 100)]),
  );
}

export function computeConfidence(
  scores: Record<string, number>,
  winnerKey?: string,
): number {
  const sorted = Object.entries(scores).sort((a, b) => b[1] - a[1]);
  if (sorted.length < 2) return 72;
  const top = winnerKey
    ? sorted.find(([k]) => k === winnerKey) ?? sorted[0]
    : sorted[0];
  const second = sorted.find(([k]) => k !== top[0]) ?? sorted[1];
  const gap = top[1] - second[1];
  const base = Math.round((gap / Math.max(top[1], 1)) * 100);
  return Math.min(98, Math.max(55, base + 40));
}

export function findCloseTypes(
  scores: Record<string, number>,
  results: Record<string, QuizResult>,
  winnerCode: string,
  limit = 2,
): CloseType[] {
  const breakdown = toPercentages(scores);
  return Object.entries(scores)
    .filter(([code]) => code !== winnerCode)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([code]) => ({
      code,
      title: results[code]?.title ?? code,
      score: breakdown[code] ?? 0,
    }));
}

export function enrichResult(
  base: QuizResult,
  scores: Record<string, number>,
  allResults: Record<string, QuizResult>,
): QuizResult {
  const scoreBreakdown = toPercentages(scores);
  const confidence = computeConfidence(scores, base.code);
  const closeTypes = findCloseTypes(scores, allResults, base.code);

  return {
    ...base,
    avatar: base.avatar ?? "🎯",
    summary: base.summary ?? base.description,
    strengths: base.strengths ?? base.traits.slice(0, 3),
    watchOuts: base.watchOuts ?? ["注意别过度消耗自己", "给他人留一点空间"],
    socialStyle:
      base.socialStyle ?? `你在关系中常呈现「${base.title}」式的独特气质。`,
    growthTips: base.growthTips ?? ["保持自我觉察", "在优势与盲点间找到平衡"],
    scoreBreakdown,
    confidence,
    closeTypes,
  };
}

export function pickTopCode(scores: Record<string, number>, fallback: string): string {
  return Object.entries(scores).sort((a, b) => b[1] - a[1])[0]?.[0] ?? fallback;
}

export function likertQ(
  id: string,
  text: string,
  agreeDim: string,
  disagreeDim: string,
): QuizQuestion {
  return { id, type: "likert", text, agreeDim, disagreeDim, scale: 5 };
}
