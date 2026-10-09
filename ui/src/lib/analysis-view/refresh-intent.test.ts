import { expect, test } from "vitest";

import { refreshIntent } from "./refresh-intent";
test("refresh is an explicit comparison and positive revision intent", () => {
  expect(
    refreshIntent(
      new URLSearchParams(
        "refresh_comparison=4f4b846b-fb89-40a6-9092-b987d43c3d67&expected_revision=2",
      ),
    ),
  ).toEqual({ id: "4f4b846b-fb89-40a6-9092-b987d43c3d67", revision: 2 });
  expect(
    refreshIntent(new URLSearchParams("refresh_comparison=bad&expected_revision=2")),
  ).toBeNull();
  expect(
    refreshIntent(
      new URLSearchParams(
        "refresh_comparison=4f4b846b-fb89-40a6-9092-b987d43c3d67&expected_revision=0",
      ),
    ),
  ).toBeNull();
});
