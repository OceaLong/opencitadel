export type QuizOption = {
  id: string;
  text: string;
  weights: Record<string, number>;
};

export type QuizQuestion = {
  id: string;
  text: string;
  options: QuizOption[];
};

export type QuizResult = {
  code: string;
  title: string;
  description: string;
  traits: string[];
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
