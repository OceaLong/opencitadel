import assert from "node:assert/strict";
import test from "node:test";

import { canonicalDigest, canonicalJson } from "../lib/canonical.mjs";

test("canonicalJson recursively orders object keys without reordering arrays", () => {
  const value = { z: [{ beta: 2, alpha: 1 }], b: 2, a: 1 };

  assert.equal(canonicalJson(value), '{"a":1,"b":2,"z":[{"alpha":1,"beta":2}]}');
});

test("canonicalDigest has a hand-verified SHA-256 value", () => {
  assert.equal(
    canonicalDigest({ b: 2, a: 1 }),
    "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777",
  );
});

test("canonicalJson rejects values that JSON cannot represent deterministically", () => {
  assert.throws(() => canonicalJson({ value: Number.NaN }), /finite number/);
  assert.throws(() => canonicalJson({ value: undefined }), /unsupported value/);

  const cyclic = {};
  cyclic.self = cyclic;
  assert.throws(() => canonicalJson(cyclic), /cyclic value/);
});
