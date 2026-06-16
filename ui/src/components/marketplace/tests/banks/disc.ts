import { enrichResult, likertQ, pickTopCode } from "../scoring";
import type { QuizBank } from "../types";

const DISC_RESULTS: QuizBank["results"] = {
  D: { code: "D", title: "支配型 (红)", description: "果断、直接、结果导向，喜欢挑战和掌控。", traits: ["领导力", "竞争", "高效"], avatar: "🔴" },
  I: { code: "I", title: "影响型 (黄)", description: "热情、乐观、善于社交，擅长激励他人。", traits: ["感染力", "创意", "乐观"], avatar: "🟡" },
  S: { code: "S", title: "稳健型 (绿)", description: "耐心、可靠、团队合作，重视稳定与和谐。", traits: ["忠诚", "耐心", "支持"], avatar: "🟢" },
  C: { code: "C", title: "谨慎型 (蓝)", description: "精确、分析、注重质量，追求准确和标准。", traits: ["细致", "逻辑", "完美主义"], avatar: "🔵" },
};

function computeDisc(scores: Record<string, number>) {
  const top = pickTopCode(scores, "S");
  const base = DISC_RESULTS[top] ?? DISC_RESULTS.S;
  return enrichResult(base, scores, DISC_RESULTS);
}

export const discBank: QuizBank = {
  id: "disc",
  name: "性格色彩 DISC",
  icon: "🎨",
  description: "红蓝黄绿四色性格，读懂你的行为风格",
  results: DISC_RESULTS,
  computeResult: computeDisc,
  questions: [
    likertQ("q1", "遇到问题时，我会立刻采取行动", "D", "S"),
    likertQ("q2", "我喜欢通过交流激发团队热情", "I", "C"),
    likertQ("q3", "我更重视稳定与和谐，不喜欢突变", "S", "D"),
    likertQ("q4", "做事前我会仔细核对细节和标准", "C", "I"),
    likertQ("q5", "我享受竞争并追求结果", "D", "S"),
    likertQ("q6", "我善于用幽默和故事影响他人", "I", "C"),
    likertQ("q7", "我愿意耐心支持团队成员", "S", "D"),
    likertQ("q8", "我对错误和低质量容忍度很低", "C", "I"),
    likertQ("q9", "我说话直接，不喜欢绕弯子", "D", "S"),
    likertQ("q10", "我是团队里的气氛担当", "I", "C"),
    likertQ("q11", "突然的变化会让我不太适应", "S", "D"),
    likertQ("q12", "我会反复检查确保万无一失", "C", "I"),
  ],
};
