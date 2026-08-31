import { expect } from "@playwright/test";

type PollOptions = {
  timeout?: number;
  intervals?: number[];
  message?: string;
};

export async function pollProjection<T>(
  read: () => Promise<T>,
  accepts: (value: T) => boolean | Promise<boolean>,
  options: PollOptions = {},
): Promise<T> {
  let latest: T | undefined;
  await expect
    .poll(
      async () => {
        latest = await read();
        return accepts(latest);
      },
      {
        timeout: options.timeout ?? 30_000,
        intervals: options.intervals ?? [100, 250, 500, 1_000],
        message: options.message,
      },
    )
    .toBe(true);
  return latest as T;
}
