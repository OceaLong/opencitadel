import assert from "node:assert/strict";
import test from "node:test";

import { embeddingFor, embeddingsFor } from "../lib/embedding.mjs";

test("embeddingFor returns a deterministic normalized 1536-dimensional vector", () => {
  const first = embeddingFor("OpenCitadel acceptance");
  const second = embeddingFor("OpenCitadel acceptance");
  const norm = Math.sqrt(first.reduce((total, value) => total + value * value, 0));

  assert.equal(first.length, 1536);
  assert.deepEqual(first, second);
  assert.ok(first.every(Number.isFinite));
  assert.ok(Math.abs(norm - 1) < 1e-12);
});

test("embeddingFor separates distinct Unicode inputs", () => {
  assert.notDeepEqual(embeddingFor("知识库"), embeddingFor("代码库"));
});

test("embeddingsFor preserves input order and rejects invalid inputs", () => {
  const vectors = embeddingsFor(["first", "second"]);

  assert.equal(vectors.length, 2);
  assert.deepEqual(vectors[0], embeddingFor("first"));
  assert.deepEqual(vectors[1], embeddingFor("second"));
  assert.throws(() => embeddingsFor(["valid", 3]), /string/);
  assert.throws(() => embeddingFor("value", 0), /positive integer/);
});
