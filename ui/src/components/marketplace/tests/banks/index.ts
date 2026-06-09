import { discBank } from "./disc";
import { enneagramBank } from "./enneagram";
import { eqBank } from "./eq";
import { funAnimalBank } from "./fun-animal";
import { loveBank } from "./love";
import { mbtiBank } from "./mbti";
import type { QuizBank } from "../types";

export const ALL_BANKS: QuizBank[] = [
  mbtiBank,
  enneagramBank,
  discBank,
  loveBank,
  eqBank,
  funAnimalBank,
];

export const BANKS_BY_ID = new Map(ALL_BANKS.map((b) => [b.id, b]));
