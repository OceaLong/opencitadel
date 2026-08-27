"use client";

import { useTranslations } from "next-intl";

import { StatusBadge } from "@/components/status-badge";

import type { PlatformInvitation } from "@/lib/api/admin";

export function InvitationStatusBadge({ status }: { status: PlatformInvitation["status"] }) {
  const t = useTranslations("admin");
  // Mapping (old ui/Badge → global StatusBadge), documented in task-6-report.md:
  // accepted → success (was secondary/neutral; success reads as the completed state)
  // pending  → warning (was outline; warning reads as "awaiting action")
  // expired  → destructive (unchanged)
  const variant =
    status === "accepted" ? "success" : status === "pending" ? "warning" : "destructive";
  const label =
    status === "accepted"
      ? t("inviteAccepted")
      : status === "pending"
        ? t("invitePending")
        : t("inviteExpired");
  return <StatusBadge variant={variant}>{label}</StatusBadge>;
}
