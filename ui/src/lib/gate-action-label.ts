type GateActionTranslator = (key: string, values?: Record<string, string>) => string;

/**
 * Localize HITL gate protocol tokens for chat bubble display.
 * Backend still receives/sends English tokens; this is display-only.
 */
export function formatGateActionMessage(
  message: string | undefined | null,
  gateT: GateActionTranslator,
  planT: GateActionTranslator,
): string {
  const text = (message ?? "").trim();
  if (!text) return "";

  if (text.startsWith("reject:")) {
    const reason = text.slice("reject:".length).trim();
    return reason ? `${gateT("reject")}：${reason}` : gateT("reject");
  }

  switch (text) {
    case "approve":
      return gateT("approve");
    case "approve_same":
      return gateT("approveSame");
    case "approve_with_edits":
      return planT("editSteps");
    case "takeover":
      return gateT("takeoverDone");
    case "skip":
      return gateT("skip");
    case "reject":
      return gateT("reject");
    default:
      return text;
  }
}
