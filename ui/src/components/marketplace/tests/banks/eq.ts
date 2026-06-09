import type { QuizBank } from "../types";

const EQ_RESULTS: QuizBank["results"] = {
  high: { code: "high", title: "情商达人", description: "你善于觉察和管理情绪，人际关系处理能力出色。", traits: ["共情力强", "情绪稳定", "社交高手"] },
  medium: { code: "medium", title: "情商良好", description: "你有不错的情绪觉察力，在多数场合能妥善处理。", traits: ["有觉察", "可提升", "平衡"] },
  growing: { code: "growing", title: "成长中", description: "你有提升空间，多练习情绪管理会有很大进步。", traits: ["潜力股", "需练习", "自我意识"] },
};

function computeEq(scores: Record<string, number>) {
  const total = Object.values(scores).reduce((a, b) => a + b, 0);
  if (total >= 18) return EQ_RESULTS.high;
  if (total >= 12) return EQ_RESULTS.medium;
  return EQ_RESULTS.growing;
}

export const eqBank: QuizBank = {
  id: "eq",
  name: "EQ 情商测试",
  icon: "💡",
  description: "测测你的情绪智力水平",
  results: EQ_RESULTS,
  computeResult: computeEq,
  questions: [
    { id: "q1", text: "生气时你能意识到自己在生气吗？", options: [
      { id: "a", text: "总是能", weights: { eq: 2 } },
      { id: "b", text: "事后才意识到", weights: { eq: 1 } },
      { id: "c", text: "经常意识不到", weights: { eq: 0 } },
    ]},
    { id: "q2", text: "你能准确判断别人的情绪吗？", options: [
      { id: "a", text: "很准确", weights: { eq: 2 } },
      { id: "b", text: "大概能猜", weights: { eq: 1 } },
      { id: "c", text: "经常猜错", weights: { eq: 0 } },
    ]},
    { id: "q3", text: "被批评时你的第一反应？", options: [
      { id: "a", text: "冷静听取反思", weights: { eq: 2 } },
      { id: "b", text: "有点不舒服但能接受", weights: { eq: 1 } },
      { id: "c", text: "立刻反驳或难过", weights: { eq: 0 } },
    ]},
    { id: "q4", text: "压力大时你能自我调节吗？", options: [
      { id: "a", text: "有成熟的方法", weights: { eq: 2 } },
      { id: "b", text: "有时可以", weights: { eq: 1 } },
      { id: "c", text: "很难控制", weights: { eq: 0 } },
    ]},
    { id: "q5", text: "和朋友意见不合时？", options: [
      { id: "a", text: "求同存异保持关系", weights: { eq: 2 } },
      { id: "b", text: "争论后和好", weights: { eq: 1 } },
      { id: "c", text: "容易闹僵", weights: { eq: 0 } },
    ]},
    { id: "q6", text: "你能用合适的方式表达不满吗？", options: [
      { id: "a", text: "能直接但温和地说", weights: { eq: 2 } },
      { id: "b", text: "会暗示或憋着", weights: { eq: 1 } },
      { id: "c", text: "要么爆发要么不说", weights: { eq: 0 } },
    ]},
    { id: "q7", text: "看到别人难过你会？", options: [
      { id: "a", text: "主动关心安慰", weights: { eq: 2 } },
      { id: "b", text: "不知道怎么开口", weights: { eq: 1 } },
      { id: "c", text: "觉得和自己无关", weights: { eq: 0 } },
    ]},
    { id: "q8", text: "你对自己的情绪模式了解吗？", options: [
      { id: "a", text: "很了解触发点", weights: { eq: 2 } },
      { id: "b", text: "了解一些", weights: { eq: 1 } },
      { id: "c", text: "不太了解", weights: { eq: 0 } },
    ]},
    { id: "q9", text: "在团队中遇到摩擦？", options: [
      { id: "a", text: "主动调解化解", weights: { eq: 2 } },
      { id: "b", text: "等别人处理", weights: { eq: 1 } },
      { id: "c", text: "加剧矛盾", weights: { eq: 0 } },
    ]},
    { id: "q10", text: "你能从失败中快速恢复吗？", options: [
      { id: "a", text: "能积极面对", weights: { eq: 2 } },
      { id: "b", text: "需要一段时间", weights: { eq: 1 } },
      { id: "c", text: "很久走不出来", weights: { eq: 0 } },
    ]},
  ],
};
