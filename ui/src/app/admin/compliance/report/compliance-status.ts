import type { StatusBadgeVariant } from "@/components/status-badge";

/** Five-state compliance evaluator vocabulary. */
export type ComplianceControlStatus = "pass" | "gap" | "attention" | "not_verified" | "na";

/**
 * Maps a compliance control's status to a StatusBadge variant.
 * pass = green, gap = red, attention = yellow, not_verified/na = gray.
 */
export function controlStatusVariant(
  status: string,
): Extract<StatusBadgeVariant, "success" | "destructive" | "warning" | "secondary"> {
  if (status === "pass") return "success";
  if (status === "gap") return "destructive";
  if (status === "attention") return "warning";
  return "secondary";
}
