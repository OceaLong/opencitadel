import { enrichResult, likertQ } from "../scoring";
import type { QuizBank } from "../types";

const EQ_RESULTS: QuizBank["results"] = {
  high: { code: "high", title: "情商达人", description: "你善于觉察和管理情绪，人际关系处理能力出色。", traits: ["共情力强", "情绪稳定", "社交高手"], avatar: "🌟" },
  medium: { code: "medium", title: "情商良好", description: "你有不错的情绪觉察力，在多数场合能妥善处理。", traits: ["有觉察", "可提升", "平衡"], avatar: "✨" },
  growing: { code: "growing", title: "成长中", description: "你有提升空间，多练习情绪管理会有很大进步。", traits: ["潜力股", "需练习", "自我意识"], avatar: "🌱" },
};

function computeEq(scores: Record<string, number>) {
  const total = scores.eq ?? Object.values(scores).reduce((a, b) => a + b, 0);
  let base = EQ_RESULTS.growing;
  if (total >= 28) base = EQ_RESULTS.high;
  else if (total >= 18) base = EQ_RESULTS.medium;
  return enrichResult({ ...base, scoreBreakdown: { EQ: Math.min(100, Math.round((total / 35) * 100)) } }, { eq: total }, EQ_RESULTS);
}

export const eqBank: QuizBank = {
  id: "eq",
  name: "EQ 情商测试",
  icon: "💡",
  description: "测测你的情绪智力水平",
  results: EQ_RESULTS,
  computeResult: computeEq,
  questions: [
    likertQ("q1", "生气时我能及时意识到自己在生气", "eq", "low"),
    likertQ("q2", "我能较准确判断别人的情绪状态", "eq", "low"),
    likertQ("q3", "被批评时我能先冷静听取再回应", "eq", "low"),
    likertQ("q4", "压力大时我有有效的自我调节方式", "eq", "low"),
    likertQ("q5", "和朋友意见不合时我能求同存异", "eq", "low"),
    likertQ("q6", "我能用合适的方式表达不满", "eq", "low"),
    likertQ("q7", "看到别人难过我会主动关心", "eq", "low"),
    likertQ("q8", "我了解自己的情绪触发模式", "eq", "low"),
    likertQ("q9", "团队摩擦时我愿意主动调解", "eq", "low"),
    likertQ("q10", "失败后我能较快恢复积极状态", "eq", "low"),
    likertQ("q11", "我能在冲突中保持同理心", "eq", "low"),
    likertQ("q12", "我善于用非暴力方式沟通", "eq", "low"),
  ],
};
