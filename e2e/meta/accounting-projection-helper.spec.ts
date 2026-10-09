import { test, expect, type Page } from "@playwright/test";
import { waitAccountingUsageProjection } from "../support/accounting-retention";

test("accounting waits for each linked run settlement projection and retains missing usage", async () => {
  const calls = [
    {
      run_id: "subject",
      purpose: "evaluation_subject",
      fact: {
        cost_usd: null,
        usage: { prompt_tokens: null, completion_tokens: null },
      },
    },
    {
      run_id: "judge",
      purpose: "evaluation_judge",
      fact: {
        cost_usd: "0.000542",
        usage: { prompt_tokens: 271, completion_tokens: 40 },
      },
    },
  ];
  const reads: Record<string, number> = {};
  const page = {
    evaluate: async (_callback: unknown, argument: any) => {
      const run = argument.requestPath.split("/")[2];
      reads[run] = (reads[run] ?? 0) + 1;
      const expected =
        run === "subject"
          ? {
              calls: 1,
              unknown_cost_calls: 1,
              unknown_usage_calls: 1,
              known_input_count: 0,
              known_output_count: 0,
            }
          : {
              calls: 1,
              unknown_cost_calls: 0,
              unknown_usage_calls: 0,
              known_input_count: 271,
              known_output_count: 40,
            };
      // A terminal Run can still expose dispatch-only accounting until settlement.
      const usage =
        reads[run] === 1
          ? {
              calls: 1,
              unknown_cost_calls: 1,
              unknown_usage_calls: 1,
              known_input_count: 0,
              known_output_count: 0,
            }
          : expected;
      return {
        status: 200,
        payload: {
          code: 200,
          msg: "success",
          data: {
            run: {
              status: "completed",
              usage: {
                [calls.find((call) => call.run_id === run)!.purpose]: usage,
              },
            },
          },
        },
      };
    },
  } as unknown as Page;
  await waitAccountingUsageProjection(page, { evidence: { calls } });
  expect(reads).toEqual({ subject: 1, judge: 2 });
});
