import type { QuizBank } from "../types";

const LOVE_RESULTS: QuizBank["results"] = {
  words: { code: "words", title: "肯定的言辞", description: "你通过赞美、鼓励和「我爱你」感受到被爱。", traits: ["言语表达", "认可", "鼓励"] },
  time: { code: "time", title: "精心的时刻", description: "高质量的陪伴和专注的相处让你感到被爱。", traits: ["陪伴", "专注", "共同经历"] },
  gifts: { code: "gifts", title: "接受礼物", description: "用心的礼物和惊喜是你感受爱意的方式。", traits: ["惊喜", "心意", "仪式感"] },
  acts: { code: "acts", title: "服务的行动", description: "对方为你做事、分担家务让你感到被照顾。", traits: ["实际行动", "分担", "体贴"] },
  touch: { code: "touch", title: "身体的接触", description: "拥抱、牵手等肢体接触是你主要的爱的语言。", traits: ["亲密", "温暖", "安全感"] },
};

function computeLove(scores: Record<string, number>) {
  const top = Object.entries(scores).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "time";
  return LOVE_RESULTS[top] ?? LOVE_RESULTS.time;
}

export const loveBank: QuizBank = {
  id: "love",
  name: "爱之语言",
  icon: "💕",
  description: "发现你表达和接收爱的方式",
  results: LOVE_RESULTS,
  computeResult: computeLove,
  questions: [
    { id: "q1", text: "什么让你最开心？", options: [
      { id: "a", text: "听到「你很棒」「我爱你」", weights: { words: 2 } },
      { id: "b", text: "对方放下手机专心陪你", weights: { time: 2 } },
      { id: "c", text: "收到用心的礼物", weights: { gifts: 2 } },
    ]},
    { id: "q2", text: "你表达爱意的方式？", options: [
      { id: "a", text: "写纸条或发暖心消息", weights: { words: 2 } },
      { id: "b", text: "安排约会共度时光", weights: { time: 2 } },
      { id: "c", text: "帮对方做事分担", weights: { acts: 2 } },
    ]},
    { id: "q3", text: "吵架后你希望对方？", options: [
      { id: "a", text: "认真道歉并说清楚", weights: { words: 2 } },
      { id: "b", text: "花时间好好聊聊", weights: { time: 2 } },
      { id: "c", text: "给个拥抱", weights: { touch: 2 } },
    ]},
    { id: "q4", text: "最让你失望的是？", options: [
      { id: "a", text: "从不夸你", weights: { words: 2 } },
      { id: "b", text: "总是忙没空陪你", weights: { time: 2 } },
      { id: "c", text: "从不记得纪念日", weights: { gifts: 2 } },
    ]},
    { id: "q5", text: "理想的约会？", options: [
      { id: "a", text: "深聊到半夜", weights: { time: 2 } },
      { id: "b", text: "一起做饭打扫", weights: { acts: 2 } },
      { id: "c", text: "牵手散步看电影", weights: { touch: 2 } },
    ]},
    { id: "q6", text: "你更看重？", options: [
      { id: "a", text: "对方说的话", weights: { words: 2 } },
      { id: "b", text: "对方为你做的事", weights: { acts: 2 } },
      { id: "c", text: "对方送的小惊喜", weights: { gifts: 2 } },
    ]},
    { id: "q7", text: "异地恋你最想念？", options: [
      { id: "a", text: "对方的鼓励和肯定", weights: { words: 2 } },
      { id: "b", text: "一起度过的时光", weights: { time: 2 } },
      { id: "c", text: "拥抱和牵手的感觉", weights: { touch: 2 } },
    ]},
    { id: "q8", text: "生日你最想要？", options: [
      { id: "a", text: "一封手写信", weights: { words: 2 } },
      { id: "b", text: "一整天专属陪伴", weights: { time: 2 } },
      { id: "c", text: "精心准备的礼物", weights: { gifts: 2 } },
    ]},
  ],
};
