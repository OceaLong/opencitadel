import type { QuizBank } from "../types";

const ENNEAGRAM_RESULTS: QuizBank["results"] = {
  "1": { code: "1", title: "完美主义者", description: "理性、有原则，追求正确与完美。", traits: ["自律", "正直", "高标准"] },
  "2": { code: "2", title: "助人者", description: "温暖、关怀，乐于付出和帮助他人。", traits: ["慷慨", "热情", "善解人意"] },
  "3": { code: "3", title: "成就者", description: "适应力强、注重形象，追求成功与认可。", traits: ["高效", "自信", "目标导向"] },
  "4": { code: "4", title: "浪漫主义者", description: "敏感、独特，渴望被理解和表达自我。", traits: ["创意", "深情", "审美"] },
  "5": { code: "5", title: "观察者", description: "好奇、独立，喜欢深入思考和积累知识。", traits: ["理性", "专注", "客观"] },
  "6": { code: "6", title: "忠诚者", description: "负责、警觉，重视安全感和归属感。", traits: ["可靠", "谨慎", "忠诚"] },
  "7": { code: "7", title: "享乐主义者", description: "乐观、多才多艺，追求新鲜体验和自由。", traits: ["活力", "好奇", "乐观"] },
  "8": { code: "8", title: "挑战者", description: "自信、果断，喜欢掌控和保护弱者。", traits: ["强势", "直接", "保护欲"] },
  "9": { code: "9", title: "和平主义者", description: "随和、包容，追求内心平静与和谐。", traits: ["温和", "耐心", "调解"] },
};

function computeEnneagram(scores: Record<string, number>) {
  const top = Object.entries(scores).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "9";
  return ENNEAGRAM_RESULTS[top] ?? ENNEAGRAM_RESULTS["9"];
}

export const enneagramBank: QuizBank = {
  id: "enneagram",
  name: "九型人格",
  icon: "🔮",
  description: "了解你的核心动机与行为模式",
  results: ENNEAGRAM_RESULTS,
  computeResult: computeEnneagram,
  questions: [
    { id: "q1", text: "你最害怕的是？", options: [
      { id: "a", text: "犯错或不完美", weights: { "1": 2 } },
      { id: "b", text: "不被需要", weights: { "2": 2 } },
      { id: "c", text: "没有价值", weights: { "3": 2 } },
    ]},
    { id: "q2", text: "你觉得自己与众不同是因为？", options: [
      { id: "a", text: "我有独特的感受和审美", weights: { "4": 2 } },
      { id: "b", text: "我比别人更懂知识", weights: { "5": 2 } },
      { id: "c", text: "我比别人更忠诚可靠", weights: { "6": 2 } },
    ]},
    { id: "q3", text: "压力大时你会？", options: [
      { id: "a", text: "找乐子转移注意力", weights: { "7": 2 } },
      { id: "b", text: "变得更强势想控制局面", weights: { "8": 2 } },
      { id: "c", text: "回避冲突保持平静", weights: { "9": 2 } },
    ]},
    { id: "q4", text: "别人最常夸你？", options: [
      { id: "a", text: "做事认真有条理", weights: { "1": 2 } },
      { id: "b", text: "热心肠会照顾人", weights: { "2": 2 } },
      { id: "c", text: "能力强有干劲", weights: { "3": 2 } },
    ]},
    { id: "q5", text: "你理想的周末是？", options: [
      { id: "a", text: "独自创作或欣赏艺术", weights: { "4": 2 } },
      { id: "b", text: "研究感兴趣的话题", weights: { "5": 2 } },
      { id: "c", text: "和信任的朋友小聚", weights: { "6": 2 } },
    ]},
    { id: "q6", text: "面对新机会你？", options: [
      { id: "a", text: "兴奋地想全部尝试", weights: { "7": 2 } },
      { id: "b", text: "评估后果断行动", weights: { "8": 2 } },
      { id: "c", text: "顺其自然不着急", weights: { "9": 2 } },
    ]},
    { id: "q7", text: "你内心最深的渴望是？", options: [
      { id: "a", text: "做正确的事", weights: { "1": 2 } },
      { id: "b", text: "被爱和需要", weights: { "2": 2 } },
      { id: "c", text: "被认可和成功", weights: { "3": 2 } },
    ]},
    { id: "q8", text: "社交场合你通常？", options: [
      { id: "a", text: "感到自己有点特别", weights: { "4": 2 } },
      { id: "b", text: "观察多于参与", weights: { "5": 2 } },
      { id: "c", text: "担心会不会出问题", weights: { "6": 2 } },
    ]},
    { id: "q9", text: "你处理无聊的方式？", options: [
      { id: "a", text: "立刻找新刺激", weights: { "7": 2 } },
      { id: "b", text: "自己找事做", weights: { "8": 2 } },
      { id: "c", text: "发呆或睡觉", weights: { "9": 2 } },
    ]},
  ],
};
