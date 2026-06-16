export type QuizOption = {
  id: string;
  text: string;
  weights: Record<string, number>;
};

export type ChoiceQuestion = {
  id: string;
  type?: "choice";
  text: string;
  options: QuizOption[];
};

export type LikertQuestion = {
  id: string;
  type: "likert";
  text: string;
  agreeDim: string;
  disagreeDim: string;
  scale?: 5 | 7;
};

export type QuizQuestion = ChoiceQuestion | LikertQuestion;

export type CloseType = {
  code: string;
  title: string;
  score: number;
};

export type QuizResult = {
  code: string;
  title: string;
  description: string;
  traits: string[];
  avatar?: string;
  summary?: string;
  strengths?: string[];
  watchOuts?: string[];
  socialStyle?: string;
  growthTips?: string[];
  scoreBreakdown?: Record<string, number>;
  confidence?: number;
  closeTypes?: CloseType[];
};

export type QuizBank = {
  id: string;
  name: string;
  icon: string;
  description: string;
  questions: QuizQuestion[];
  results: Record<string, QuizResult>;
  computeResult: (scores: Record<string, number>) => QuizResult;
};

export const LIKERT_LABELS_5 = [
  "非常不同意",
  "不同意",
  "中立",
  "同意",
  "非常同意",
] as const;

export const LIKERT_LABELS_7 = [
  "非常不同意",
  "不同意",
  "略不同意",
  "中立",
  "略同意",
  "同意",
  "非常同意",
] as const;

export function isLikertQuestion(q: QuizQuestion): q is LikertQuestion {
  return q.type === "likert";
}

export function isChoiceQuestion(q: QuizQuestion): q is ChoiceQuestion {
  return !q.type || q.type === "choice";
}
