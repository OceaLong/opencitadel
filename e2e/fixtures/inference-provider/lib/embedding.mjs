import { createHash } from "node:crypto";

const DEFAULT_DIMENSIONS = 1536;
const MAX_DIMENSIONS = 8192;

function validateDimensions(dimensions) {
  if (!Number.isInteger(dimensions) || dimensions <= 0 || dimensions > MAX_DIMENSIONS) {
    throw new TypeError(`dimensions must be a positive integer no greater than ${MAX_DIMENSIONS}`);
  }
}

export function embeddingFor(text, dimensions = DEFAULT_DIMENSIONS) {
  if (typeof text !== "string") throw new TypeError("embedding input must be a string");
  validateDimensions(dimensions);

  const vector = [];
  for (let block = 0; vector.length < dimensions; block += 1) {
    const digest = createHash("sha256").update(`${text}\u0000${block}`, "utf8").digest();
    for (const byte of digest) {
      if (vector.length === dimensions) break;
      vector.push((byte - 127.5) / 127.5);
    }
  }
  const norm = Math.sqrt(vector.reduce((total, value) => total + value * value, 0));
  if (!Number.isFinite(norm) || norm === 0) throw new Error("embedding normalization failed");
  return vector.map((value) => value / norm);
}

export function embeddingsFor(input, dimensions = DEFAULT_DIMENSIONS) {
  const values = typeof input === "string" ? [input] : input;
  if (!Array.isArray(values) || values.length === 0) {
    throw new TypeError("embedding input must be a string or a non-empty string array");
  }
  return values.map((value) => embeddingFor(value, dimensions));
}
