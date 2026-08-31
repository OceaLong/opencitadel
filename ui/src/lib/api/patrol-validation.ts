import type { PatrolPack } from "./types";

type ValidationWaitOptions = {
  attempts?: number;
  delay?: () => Promise<void>;
};

const defaultDelay = () => new Promise<void>((resolve) => setTimeout(resolve, 500));

export async function waitForPackValidation(
  load: () => Promise<PatrolPack>,
  { attempts = 240, delay = defaultDelay }: ValidationWaitOptions = {},
): Promise<PatrolPack> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const pack = await load();
    if (pack.status !== "validating") return pack;
    if (attempt < attempts - 1) await delay();
  }
  throw new Error("Patrol validation did not reach a terminal state");
}
