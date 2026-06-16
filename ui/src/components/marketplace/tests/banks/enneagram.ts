import { enrichResult, likertQ, pickTopCode } from "../scoring";
import type { QuizBank } from "../types";

const ENNEAGRAM_RESULTS: QuizBank["results"] = {
  "1": { code: "1", title: "完美主义者", description: "理性、有原则，追求正确与完美。", traits: ["自律", "正直", "高标准"], avatar: "⚖️" },
  "2": { code: "2", title: "助人者", description: "温暖、关怀，乐于付出和帮助他人。", traits: ["慷慨", "热情", "善解人意"], avatar: "💝" },
  "3": { code: "3", title: "成就者", description: "适应力强、注重形象，追求成功与认可。", traits: ["高效", "自信", "目标导向"], avatar: "🏆" },
  "4": { code: "4", title: "浪漫主义者", description: "敏感、独特，渴望被理解和表达自我。", traits: ["创意", "深情", "审美"], avatar: "🎭" },
  "5": { code: "5", title: "观察者", description: "好奇、独立，喜欢深入思考和积累知识。", traits: ["理性", "专注", "客观"], avatar: "🔭" },
  "6": { code: "6", title: "忠诚者", description: "负责、警觉，重视安全感和归属感。", traits: ["可靠", "谨慎", "忠诚"], avatar: "🛡️" },
  "7": { code: "7", title: "享乐主义者", description: "乐观、多才多艺，追求新鲜体验和自由。", traits: ["活力", "好奇", "乐观"], avatar: "🎢" },
  "8": { code: "8", title: "挑战者", description: "自信、果断，喜欢掌控和保护弱者。", traits: ["强势", "直接", "保护欲"], avatar: "🦁" },
  "9": { code: "9", title: "和平主义者", description: "随和、包容，追求内心平静与和谐。", traits: ["温和", "耐心", "调解"], avatar: "☮️" },
};

function computeEnneagram(scores: Record<string, number>) {
  const top = pickTopCode(scores, "9");
  const base = ENNEAGRAM_RESULTS[top] ?? ENNEAGRAM_RESULTS["9"];
  return enrichResult(base, scores, ENNEAGRAM_RESULTS);
}

export const enneagramBank: QuizBank = {
  id: "enneagram",
  name: "九型人格",
  icon: "🔮",
  description: "了解你的核心动机与行为模式",
  results: ENNEAGRAM_RESULTS,
  computeResult: computeEnneagram,
  questions: [
    likertQ("q1", "我很在意事情是否做得正确、符合标准", "1", "7"),
    likertQ("q2", "我乐于照顾他人，希望被需要和认可", "2", "5"),
    likertQ("q3", "我重视成就和他人对我的评价", "3", "9"),
    likertQ("q4", "我常感到自己与众不同，情绪体验很深", "4", "3"),
    likertQ("q5", "我喜欢独处思考，积累知识和见解", "5", "2"),
    likertQ("q6", "我会提前考虑风险，重视安全感", "6", "7"),
    likertQ("q7", "我追求新鲜体验，讨厌单调和束缚", "7", "1"),
    likertQ("q8", "我习惯直接表达立场，不喜欢被控制", "8", "9"),
    likertQ("q9", "我倾向避免冲突，维持和谐与平静", "9", "8"),
    likertQ("q10", "犯错会让我长时间自责", "1", "7"),
    likertQ("q11", "别人的需求常优先于我自己的", "2", "5"),
    likertQ("q12", "我会为目标高效行动，展示最好的一面", "3", "9"),
    likertQ("q13", "艺术和美感对我很重要", "4", "3"),
    likertQ("q14", "社交场合我更像观察者而非主角", "5", "2"),
    likertQ("q15", "面对不确定时我会反复确认", "6", "7"),
  ],
};
