import type { QuizBank } from "../types";

const MBTI_RESULTS: QuizBank["results"] = {
  INTJ: { code: "INTJ", title: "建筑师", description: "富有想象力和战略性的思想家，一切皆在计划之中。", traits: ["独立", "理性", "有远见"] },
  INTP: { code: "INTP", title: "逻辑学家", description: "具有创造力的发明家，对知识有着止不住的渴望。", traits: ["好奇", "分析", "创新"] },
  ENTJ: { code: "ENTJ", title: "指挥官", description: "大胆、富有想象力且意志强大的领导者。", traits: ["果断", "自信", "高效"] },
  ENTP: { code: "ENTP", title: "辩论家", description: "聪明好奇的思想者，不会放过任何智力挑战。", traits: ["机智", "灵活", "挑战者"] },
  INFJ: { code: "INFJ", title: "提倡者", description: "安静而神秘，同时鼓舞人心且不知疲倦的理想主义者。", traits: ["洞察", "理想", "坚定"] },
  INFP: { code: "INFP", title: "调停者", description: "诗意、善良的利他主义者，总是热情地为正义事业提供帮助。", traits: ["共情", "创意", "真诚"] },
  ENFJ: { code: "ENFJ", title: "主人公", description: "富有魅力且鼓舞人心的领导者，有能力让听众着迷。", traits: ["热情", "利他", "领导力"] },
  ENFP: { code: "ENFP", title: "竞选者", description: "热情、有创造力且社交能力强的自由精神。", traits: ["热情", "想象力", "社交"] },
  ISTJ: { code: "ISTJ", title: "物流师", description: "实际且注重事实的个人，可靠性不容怀疑。", traits: ["可靠", "务实", "有条理"] },
  ISFJ: { code: "ISFJ", title: "守卫者", description: "非常专注且温暖的守护者，时刻准备着保护所爱之人。", traits: ["忠诚", "细心", "奉献"] },
  ESTJ: { code: "ESTJ", title: "总经理", description: "出色的管理者，在管理事务或人员方面无与伦比。", traits: ["组织", "直接", "负责"] },
  ESFJ: { code: "ESFJ", title: "执政官", description: "极有同情心、受欢迎且总是热心提供帮助的人。", traits: ["关怀", "合作", "传统"] },
  ISTP: { code: "ISTP", title: "鉴赏家", description: "大胆而实际的实验家，擅长使用各类工具。", traits: ["冷静", "实用", "灵活"] },
  ISFP: { code: "ISFP", title: "探险家", description: "灵活且迷人的艺术家，时刻准备着探索和体验新事物。", traits: ["艺术", "温和", "自由"] },
  ESTP: { code: "ESTP", title: "企业家", description: "聪明、精力充沛且善于感知的人，真心享受生活在边缘。", traits: ["大胆", "直接", "行动派"] },
  ESFP: { code: "ESFP", title: "表演者", description: "自发的、精力充沛且热情的表演者，生活永远不会无聊。", traits: ["活力", "乐观", "社交"] },
};

function computeMbti(scores: Record<string, number>) {
  const code =
    (scores.E >= scores.I ? "E" : "I") +
    (scores.S >= scores.N ? "S" : "N") +
    (scores.T >= scores.F ? "T" : "F") +
    (scores.J >= scores.P ? "J" : "P");
  return MBTI_RESULTS[code] ?? MBTI_RESULTS.INFP;
}

export const mbtiBank: QuizBank = {
  id: "mbti",
  name: "MBTI 16型人格",
  icon: "🧠",
  description: "探索你的认知偏好，发现属于你的 16 型人格",
  results: MBTI_RESULTS,
  computeResult: computeMbti,
  questions: [
    { id: "q1", text: "参加聚会后，你通常感觉？", options: [
      { id: "a", text: "精力充沛，想继续社交", weights: { E: 2 } },
      { id: "b", text: "有点累，想独处充电", weights: { I: 2 } },
    ]},
    { id: "q2", text: "你更关注？", options: [
      { id: "a", text: "具体事实和细节", weights: { S: 2 } },
      { id: "b", text: "整体模式和可能性", weights: { N: 2 } },
    ]},
    { id: "q3", text: "做决定时你更依赖？", options: [
      { id: "a", text: "逻辑和客观分析", weights: { T: 2 } },
      { id: "b", text: "价值观和他人感受", weights: { F: 2 } },
    ]},
    { id: "q4", text: "你更喜欢？", options: [
      { id: "a", text: "提前计划，按部就班", weights: { J: 2 } },
      { id: "b", text: "灵活应变，保持开放", weights: { P: 2 } },
    ]},
    { id: "q5", text: "周末你更愿意？", options: [
      { id: "a", text: "和朋友外出活动", weights: { E: 2 } },
      { id: "b", text: "在家看书或追剧", weights: { I: 2 } },
    ]},
    { id: "q6", text: "学习新东西时你偏好？", options: [
      { id: "a", text: "按步骤实操", weights: { S: 2 } },
      { id: "b", text: "先理解原理再实践", weights: { N: 2 } },
    ]},
    { id: "q7", text: "朋友向你倾诉烦恼，你会？", options: [
      { id: "a", text: "帮他分析问题找方案", weights: { T: 2 } },
      { id: "b", text: "先安慰和共情", weights: { F: 2 } },
    ]},
    { id: "q8", text: "你的工作桌面通常是？", options: [
      { id: "a", text: "整洁有序", weights: { J: 2 } },
      { id: "b", text: "创意凌乱但能找到东西", weights: { P: 2 } },
    ]},
    { id: "q9", text: "在团队中你更常？", options: [
      { id: "a", text: "主动发言带动气氛", weights: { E: 2 } },
      { id: "b", text: "倾听后深思熟虑再发言", weights: { I: 2 } },
    ]},
    { id: "q10", text: "你更相信？", options: [
      { id: "a", text: "经验证明过的方法", weights: { S: 2 } },
      { id: "b", text: "直觉和灵感", weights: { N: 2 } },
    ]},
    { id: "q11", text: "面对冲突你倾向于？", options: [
      { id: "a", text: "就事论事讲道理", weights: { T: 2 } },
      { id: "b", text: "维护关系和和谐", weights: { F: 2 } },
    ]},
    { id: "q12", text: "旅行时你更喜欢？", options: [
      { id: "a", text: "详细行程表", weights: { J: 2 } },
      { id: "b", text: "走到哪算哪", weights: { P: 2 } },
    ]},
  ],
};
