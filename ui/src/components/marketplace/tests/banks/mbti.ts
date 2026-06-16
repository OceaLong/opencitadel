import { enrichResult, likertQ } from "../scoring";
import type { QuizBank } from "../types";

const MBTI_AVATARS: Record<string, string> = {
  INTJ: "🏛️", INTP: "🔬", ENTJ: "👑", ENTP: "💡",
  INFJ: "🌙", INFP: "🌸", ENFJ: "🌟", ENFP: "🎈",
  ISTJ: "📋", ISFJ: "🛡️", ESTJ: "📊", ESFJ: "🤝",
  ISTP: "🔧", ISFP: "🎨", ESTP: "⚡", ESFP: "🎭",
};

const MBTI_RESULTS: QuizBank["results"] = {
  INTJ: { code: "INTJ", title: "建筑师", description: "富有想象力和战略性的思想家，一切皆在计划之中。", traits: ["独立", "理性", "有远见"], avatar: "🏛️", strengths: ["战略思维", "专注执行", "系统规划"], watchOuts: ["过于理想化", "情感表达不足"], socialStyle: "你偏好深度交流，重视逻辑与远见。", growthTips: ["适当分享感受", "给团队更多情感反馈"] },
  INTP: { code: "INTP", title: "逻辑学家", description: "具有创造力的发明家，对知识有着止不住的渴望。", traits: ["好奇", "分析", "创新"], avatar: "🔬" },
  ENTJ: { code: "ENTJ", title: "指挥官", description: "大胆、富有想象力且意志强大的领导者。", traits: ["果断", "自信", "高效"], avatar: "👑" },
  ENTP: { code: "ENTP", title: "辩论家", description: "聪明好奇的思想者，不会放过任何智力挑战。", traits: ["机智", "灵活", "挑战者"], avatar: "💡" },
  INFJ: { code: "INFJ", title: "提倡者", description: "安静而神秘，同时鼓舞人心且不知疲倦的理想主义者。", traits: ["洞察", "理想", "坚定"], avatar: "🌙" },
  INFP: { code: "INFP", title: "调停者", description: "诗意、善良的利他主义者，总是热情地为正义事业提供帮助。", traits: ["共情", "创意", "真诚"], avatar: "🌸" },
  ENFJ: { code: "ENFJ", title: "主人公", description: "富有魅力且鼓舞人心的领导者，有能力让听众着迷。", traits: ["热情", "利他", "领导力"], avatar: "🌟" },
  ENFP: { code: "ENFP", title: "竞选者", description: "热情、有创造力且社交能力强的自由精神。", traits: ["热情", "想象力", "社交"], avatar: "🎈" },
  ISTJ: { code: "ISTJ", title: "物流师", description: "实际且注重事实的个人，可靠性不容怀疑。", traits: ["可靠", "务实", "有条理"], avatar: "📋" },
  ISFJ: { code: "ISFJ", title: "守卫者", description: "非常专注且温暖的守护者，时刻准备着保护所爱之人。", traits: ["忠诚", "细心", "奉献"], avatar: "🛡️" },
  ESTJ: { code: "ESTJ", title: "总经理", description: "出色的管理者，在管理事务或人员方面无与伦比。", traits: ["组织", "直接", "负责"], avatar: "📊" },
  ESFJ: { code: "ESFJ", title: "执政官", description: "极有同情心、受欢迎且总是热心提供帮助的人。", traits: ["关怀", "合作", "传统"], avatar: "🤝" },
  ISTP: { code: "ISTP", title: "鉴赏家", description: "大胆而实际的实验家，擅长使用各类工具。", traits: ["冷静", "实用", "灵活"], avatar: "🔧" },
  ISFP: { code: "ISFP", title: "探险家", description: "灵活且迷人的艺术家，时刻准备着探索和体验新事物。", traits: ["艺术", "温和", "自由"], avatar: "🎨" },
  ESTP: { code: "ESTP", title: "企业家", description: "聪明、精力充沛且善于感知的人，真心享受生活在边缘。", traits: ["大胆", "直接", "行动派"], avatar: "⚡" },
  ESFP: { code: "ESFP", title: "表演者", description: "自发的、精力充沛且热情的表演者，生活永远不会无聊。", traits: ["活力", "乐观", "社交"], avatar: "🎭" },
};

function computeMbti(scores: Record<string, number>) {
  const code =
    (scores.E >= scores.I ? "E" : "I") +
    (scores.S >= scores.N ? "S" : "N") +
    (scores.T >= scores.F ? "T" : "F") +
    (scores.J >= scores.P ? "J" : "P");
  const base = MBTI_RESULTS[code] ?? MBTI_RESULTS.INFP;
  return enrichResult(
    { ...base, avatar: base.avatar ?? MBTI_AVATARS[code] ?? "🧠" },
    scores,
    MBTI_RESULTS,
  );
}

export const mbtiBank: QuizBank = {
  id: "mbti",
  name: "MBTI 16型人格",
  icon: "🧠",
  description: "探索你的认知偏好，发现属于你的 16 型人格",
  results: MBTI_RESULTS,
  computeResult: computeMbti,
  questions: [
    likertQ("q1", "参加聚会后，我通常感到精力充沛，还想继续社交", "E", "I"),
    likertQ("q2", "我更容易被具体事实和细节吸引", "S", "N"),
    likertQ("q3", "做决定时，我更依赖逻辑和客观分析", "T", "F"),
    likertQ("q4", "我喜欢提前计划，按部就班地推进", "J", "P"),
    likertQ("q5", "周末我更愿意和朋友外出活动", "E", "I"),
    likertQ("q6", "学习新东西时，我偏好先理解原理再实践", "N", "S"),
    likertQ("q7", "朋友倾诉时，我会先帮他分析问题找方案", "T", "F"),
    likertQ("q8", "我的工作桌面通常整洁有序", "J", "P"),
    likertQ("q9", "在团队中我常主动发言带动气氛", "E", "I"),
    likertQ("q10", "我更相信直觉和灵感，而非仅看经验", "N", "S"),
    likertQ("q11", "面对冲突时，我更倾向就事论事讲道理", "T", "F"),
    likertQ("q12", "旅行时我更喜欢详细行程表", "J", "P"),
    likertQ("q13", "长时间独处后，我会渴望与人交流", "E", "I"),
    likertQ("q14", "我常会思考事物的深层含义和未来可能", "N", "S"),
    likertQ("q15", "维护关系和和谐对我很重要", "F", "T"),
    likertQ("q16", "我更喜欢有截止期限和明确目标的任务", "J", "P"),
    likertQ("q17", "认识新朋友让我充满动力", "E", "I"),
    likertQ("q18", "我关注整体模式和可能性多于细节", "N", "S"),
    likertQ("q19", "我会优先考虑事情是否公平合理", "T", "F"),
    likertQ("q20", "我习惯把待办事项列成清单", "J", "P"),
  ],
};
