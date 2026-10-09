export type DiffDocument = {
  format: string;
  left: Record<string, unknown>;
  right: Record<string, unknown>;
  diff: {
    content_changed: boolean | null;
    complete: boolean;
    reason: string | null;
    content: string;
    operations: Record<string, unknown>[];
    before_preview?: string;
    after_preview?: string;
  };
};
const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);
export function readDiffDocument(chunks: readonly string[]): DiffDocument {
  if (chunks.length > 16 || chunks.some((c) => new TextEncoder().encode(c).length > 65536))
    throw new Error("diff_limit");
  const value: unknown = JSON.parse(chunks.join(""));
  if (
    !object(value) ||
    typeof value.format !== "string" ||
    !object(value.left) ||
    !object(value.right) ||
    !object(value.diff)
  )
    throw new Error("invalid_diff");
  const d = value.diff;
  if (
    typeof d.complete !== "boolean" ||
    (d.content_changed !== null && typeof d.content_changed !== "boolean") ||
    (d.reason !== null && typeof d.reason !== "string") ||
    typeof d.content !== "string" ||
    !Array.isArray(d.operations) ||
    !d.operations.every(object)
  )
    throw new Error("invalid_diff");
  if (
    (d.before_preview !== undefined && typeof d.before_preview !== "string") ||
    (d.after_preview !== undefined && typeof d.after_preview !== "string")
  )
    throw new Error("invalid_diff");
  return {
    format: value.format,
    left: value.left,
    right: value.right,
    diff: {
      complete: d.complete,
      content_changed: d.content_changed,
      reason: d.reason,
      content: d.content,
      operations: d.operations,
      before_preview: d.before_preview as string | undefined,
      after_preview: d.after_preview as string | undefined,
    },
  };
}
