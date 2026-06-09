import type { QuizBank } from "../types";

const ANIMAL_RESULTS: QuizBank["results"] = {
  cat: { code: "cat", title: "慵懒猫咪", description: "独立又傲娇，享受独处但也渴望被宠爱。", traits: ["独立", "优雅", "神秘"] },
  dog: { code: "dog", title: "热情修狗", description: "忠诚活泼，是团队里的开心果和粘合剂。", traits: ["忠诚", "热情", "社交"] },
  owl: { code: "owl", title: "智慧猫头鹰", description: "深夜思考者，喜欢深度思考和安静环境。", traits: ["智慧", "冷静", "观察"] },
  fox: { code: "fox", title: "机灵狐狸", description: "聪明灵活，善于应变和找到捷径。", traits: ["机智", "灵活", "好奇"] },
  panda: { code: "panda", title: "佛系熊猫", description: "温和随性，追求舒适和内心的平和。", traits: ["温和", "佛系", "可爱"] },
  eagle: { code: "eagle", title: "翱翔雄鹰", description: "有野心有视野，喜欢挑战高处和目标。", traits: ["野心", "远见", "果断"] },
};

function computeAnimal(scores: Record<string, number>) {
  const top = Object.entries(scores).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "panda";
  return ANIMAL_RESULTS[top] ?? ANIMAL_RESULTS.panda;
}

export const funAnimalBank: QuizBank = {
  id: "fun-animal",
  name: "你是哪种动物",
  icon: "🐾",
  description: "轻松有趣的动物人格测试",
  results: ANIMAL_RESULTS,
  computeResult: computeAnimal,
  questions: [
    { id: "q1", text: "理想的休息日？", options: [
      { id: "a", text: "窝在家里不被打扰", weights: { cat: 2 } },
      { id: "b", text: "约朋友出去玩", weights: { dog: 2 } },
      { id: "c", text: "看书或研究感兴趣的事", weights: { owl: 2 } },
    ]},
    { id: "q2", text: "遇到难题你？", options: [
      { id: "a", text: "想个巧妙的办法绕过去", weights: { fox: 2 } },
      { id: "b", text: "先睡一觉再说", weights: { panda: 2 } },
      { id: "c", text: "正面硬刚拿下", weights: { eagle: 2 } },
    ]},
    { id: "q3", text: "朋友说你像？", options: [
      { id: "a", text: "高冷但偶尔撒娇", weights: { cat: 2 } },
      { id: "b", text: "永远精力充沛", weights: { dog: 2 } },
      { id: "c", text: "话不多但一针见血", weights: { owl: 2 } },
    ]},
    { id: "q4", text: "你的社交策略？", options: [
      { id: "a", text: "小圈子深交", weights: { cat: 2 } },
      { id: "b", text: "谁都聊得来", weights: { dog: 2 } },
      { id: "c", text: "见人说人话见鬼说鬼话", weights: { fox: 2 } },
    ]},
    { id: "q5", text: "面对竞争你？", options: [
      { id: "a", text: "懒得争，佛系就好", weights: { panda: 2 } },
      { id: "b", text: "志在必得冲第一", weights: { eagle: 2 } },
      { id: "c", text: "暗中观察找时机", weights: { fox: 2 } },
    ]},
    { id: "q6", text: "你最喜欢的时刻？", options: [
      { id: "a", text: "深夜独自思考", weights: { owl: 2 } },
      { id: "b", text: "被朋友围着夸", weights: { dog: 2 } },
      { id: "c", text: "晒太阳发呆", weights: { panda: 2 } },
    ]},
    { id: "q7", text: "你的口头禅接近？", options: [
      { id: "a", text: "随便吧都行", weights: { panda: 2 } },
      { id: "b", text: "冲！干就完了", weights: { eagle: 2 } },
      { id: "c", text: "让我想想...", weights: { owl: 2 } },
    ]},
    { id: "q8", text: "别人惹到你？", options: [
      { id: "a", text: "冷处理不理他", weights: { cat: 2 } },
      { id: "b", text: "直接说出来", weights: { dog: 2 } },
      { id: "c", text: "记在心里找机会", weights: { fox: 2 } },
    ]},
  ],
};
