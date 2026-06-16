import { enrichResult, likertQ, pickTopCode } from "../scoring";
import type { QuizBank } from "../types";

const LOVE_RESULTS: QuizBank["results"] = {
  words: { code: "words", title: "肯定的言辞", description: "你通过赞美、鼓励和「我爱你」感受到被爱。", traits: ["言语表达", "认可", "鼓励"], avatar: "💬" },
  time: { code: "time", title: "精心的时刻", description: "高质量的陪伴和专注的相处让你感到被爱。", traits: ["陪伴", "专注", "共同经历"], avatar: "⏰" },
  gifts: { code: "gifts", title: "接受礼物", description: "用心的礼物和惊喜是你感受爱意的方式。", traits: ["惊喜", "心意", "仪式感"], avatar: "🎁" },
  acts: { code: "acts", title: "服务的行动", description: "对方为你做事、分担家务让你感到被照顾。", traits: ["实际行动", "分担", "体贴"], avatar: "🤲" },
  touch: { code: "touch", title: "身体的接触", description: "拥抱、牵手等肢体接触是你主要的爱的语言。", traits: ["亲密", "温暖", "安全感"], avatar: "🤗" },
};

function computeLove(scores: Record<string, number>) {
  const top = pickTopCode(scores, "time");
  const base = LOVE_RESULTS[top] ?? LOVE_RESULTS.time;
  return enrichResult(base, scores, LOVE_RESULTS);
}

export const loveBank: QuizBank = {
  id: "love",
  name: "爱之语言",
  icon: "💕",
  description: "发现你表达和接收爱的方式",
  results: LOVE_RESULTS,
  computeResult: computeLove,
  questions: [
    likertQ("q1", "听到「你很棒」「我爱你」会让我特别开心", "words", "acts"),
    likertQ("q2", "对方放下手机专心陪我，比礼物更重要", "time", "gifts"),
    likertQ("q3", "收到用心的礼物会让我感到被重视", "gifts", "acts"),
    likertQ("q4", "对方帮我做事、分担压力让我感受到爱", "acts", "words"),
    likertQ("q5", "拥抱和牵手对我很重要", "touch", "words"),
    likertQ("q6", "我会用写纸条或暖心消息表达爱意", "words", "acts"),
    likertQ("q7", "一起度过的专注时光最能打动我", "time", "gifts"),
    likertQ("q8", "纪念日我更期待有仪式感的惊喜", "gifts", "time"),
    likertQ("q9", "我表达爱更多通过实际行动", "acts", "words"),
    likertQ("q10", "肢体接触缺失会让我感到疏远", "touch", "time"),
    likertQ("q11", "被批评时，我希望听到真诚的解释和道歉", "words", "acts"),
    likertQ("q12", "理想的约会是深度聊天而非热闹场合", "time", "gifts"),
  ],
};
