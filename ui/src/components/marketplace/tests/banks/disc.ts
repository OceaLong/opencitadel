import type { QuizBank } from "../types";

const DISC_RESULTS: QuizBank["results"] = {
  D: { code: "D", title: "支配型 (红)", description: "果断、直接、结果导向，喜欢挑战和掌控。", traits: ["领导力", "竞争", "高效"] },
  I: { code: "I", title: "影响型 (黄)", description: "热情、乐观、善于社交，擅长激励他人。", traits: ["感染力", "创意", "乐观"] },
  S: { code: "S", title: "稳健型 (绿)", description: "耐心、可靠、团队合作，重视稳定与和谐。", traits: ["忠诚", "耐心", "支持"] },
  C: { code: "C", title: "谨慎型 (蓝)", description: "精确、分析、注重质量，追求准确和标准。", traits: ["细致", "逻辑", "完美主义"] },
};

function computeDisc(scores: Record<string, number>) {
  const top = Object.entries(scores).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "S";
  return DISC_RESULTS[top] ?? DISC_RESULTS.S;
}

export const discBank: QuizBank = {
  id: "disc",
  name: "性格色彩 DISC",
  icon: "🎨",
  description: "红蓝黄绿四色性格，读懂你的行为风格",
  results: DISC_RESULTS,
  computeResult: computeDisc,
  questions: [
    { id: "q1", text: "遇到问题时你首先？", options: [
      { id: "a", text: "立刻采取行动解决", weights: { D: 2 } },
      { id: "b", text: "找人讨论想法", weights: { I: 2 } },
      { id: "c", text: "冷静观察再处理", weights: { S: 2 } },
      { id: "d", text: "收集信息分析原因", weights: { C: 2 } },
    ]},
    { id: "q2", text: "你最喜欢的赞美是？", options: [
      { id: "a", text: "你真有魄力", weights: { D: 2 } },
      { id: "b", text: "和你在一起很开心", weights: { I: 2 } },
      { id: "c", text: "你真可靠", weights: { S: 2 } },
      { id: "d", text: "你做事很严谨", weights: { C: 2 } },
    ]},
    { id: "q3", text: "团队项目中你常扮演？", options: [
      { id: "a", text: "决策者/推动者", weights: { D: 2 } },
      { id: "b", text: "气氛担当/创意来源", weights: { I: 2 } },
      { id: "c", text: "协调者/后勤支持", weights: { S: 2 } },
      { id: "d", text: "质量把关/细节把控", weights: { C: 2 } },
    ]},
    { id: "q4", text: "你最受不了的是？", options: [
      { id: "a", text: "效率低下拖后腿", weights: { D: 2 } },
      { id: "b", text: "沉闷无聊没乐趣", weights: { I: 2 } },
      { id: "c", text: "突然变化和冲突", weights: { S: 2 } },
      { id: "d", text: "马虎不精确", weights: { C: 2 } },
    ]},
    { id: "q5", text: "你的说话风格？", options: [
      { id: "a", text: "简短有力", weights: { D: 2 } },
      { id: "b", text: "生动有趣", weights: { I: 2 } },
      { id: "c", text: "温和委婉", weights: { S: 2 } },
      { id: "d", text: "条理清晰有据", weights: { C: 2 } },
    ]},
    { id: "q6", text: "面对截止日期你？", options: [
      { id: "a", text: "冲刺完成甚至提前", weights: { D: 2 } },
      { id: "b", text: "边做边聊不太焦虑", weights: { I: 2 } },
      { id: "c", text: "按部就班稳步推进", weights: { S: 2 } },
      { id: "d", text: "反复检查确保无误", weights: { C: 2 } },
    ]},
    { id: "q7", text: "你理想的领导风格？", options: [
      { id: "a", text: "放权让我自己干", weights: { D: 2 } },
      { id: "b", text: "鼓励认可多交流", weights: { I: 2 } },
      { id: "c", text: "稳定支持有安全感", weights: { S: 2 } },
      { id: "d", text: "标准明确有章可循", weights: { C: 2 } },
    ]},
    { id: "q8", text: "购物时你？", options: [
      { id: "a", text: "目标明确快速买完", weights: { D: 2 } },
      { id: "b", text: "边逛边聊享受过程", weights: { I: 2 } },
      { id: "c", text: "货比三家不着急", weights: { S: 2 } },
      { id: "d", text: "研究参数和评价", weights: { C: 2 } },
    ]},
  ],
};
