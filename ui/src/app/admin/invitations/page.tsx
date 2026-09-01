"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { InvitationStatusBadge } from "@/components/admin/invitation-status-badge";
import { AdminStatCard } from "@/components/admin/stat-card";
import { EmptyState } from "@/components/empty-state";
import { LoadingSpinner } from "@/components/loading-spinner";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { formatDateTime } from "@/lib/admin-utils";
import { adminApi, type PlatformInvitation } from "@/lib/api/admin";
import { IconCopy, IconInvitation } from "@/lib/icons";

export default function AdminInvitationsPage() {
  const t = useTranslations("admin");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const [invitations, setInvitations] = useState<PlatformInvitation[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteUrl, setInviteUrl] = useState("");
  const [creating, setCreating] = useState(false);

  async function loadInvitations() {
    setLoading(true);
    try {
      const data = await adminApi.invitations({ limit: 100 });
      setInvitations(data.invitations);
      setTotal(data.total);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadInvitations();
  }, []);

  async function createInvite() {
    if (!inviteEmail.trim()) {
      toast.error(t("enterEmail"));
      return;
    }
    setCreating(true);
    try {
      const data = await adminApi.invite(inviteEmail.trim());
      setInviteUrl(data.url);
      toast.success(t("inviteLinkGenerated"));
      setInviteEmail("");
      await loadInvitations();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("generateFailed"));
    } finally {
      setCreating(false);
    }
  }

  async function copyUrl(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      toast.success(t("inviteLinkCopied"));
    } catch {
      toast.error(tCommon("copyFailed"));
    }
  }

  const pending = invitations.filter((item) => item.status === "pending").length;
  const accepted = invitations.filter((item) => item.status === "accepted").length;
  const expired = invitations.filter((item) => item.status === "expired").length;

  return (
    <div className="space-y-6">
      <PageHeader title={t("platformInvite")} description={t("invitationsSubtitle")} />

      <div className="grid gap-3 md:grid-cols-3">
        <AdminStatCard label={t("summaryPending")} value={pending} />
        <AdminStatCard label={t("summaryAccepted")} value={accepted} />
        <AdminStatCard label={t("summaryExpired")} value={expired} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("generateInviteTitle")}</CardTitle>
          <CardDescription>{t("inviteValidDays")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={inviteEmail}
              onChange={(event) => setInviteEmail(event.target.value)}
              translate="no"
              placeholder="user@example.com"
            />
            <Button disabled={creating} onClick={() => void createInvite()}>
              {creating ? <LoadingSpinner /> : <IconInvitation className="mr-1 size-4" />}
              {t("generateInviteLink")}
            </Button>
          </div>
          {inviteUrl ? (
            <div className="bg-muted/30 flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
              <span className="truncate">{inviteUrl}</span>
              <Button variant="ghost" size="icon" onClick={() => void copyUrl(inviteUrl)}>
                <IconCopy className="size-4" />
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("invitationRecordsTitle")}</CardTitle>
          <CardDescription>{t("totalRecords", { count: total })}</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex justify-center py-12">
              <LoadingSpinner />
            </div>
          ) : invitations.length === 0 ? (
            <EmptyState title={t("noInvitationRecords")} className="py-10" />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("columnEmail")}</TableHead>
                  <TableHead>{t("columnCreatedAt")}</TableHead>
                  <TableHead>{t("columnExpiresAt")}</TableHead>
                  <TableHead className="text-right">{t("status")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invitations.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="max-w-56 truncate font-medium">
                      {item.email || t("noEmailSpecified")}
                    </TableCell>
                    <TableCell className="text-muted-foreground font-mono text-xs">
                      {formatDateTime(item.created_at, locale)}
                    </TableCell>
                    <TableCell className="text-muted-foreground font-mono text-xs">
                      {formatDateTime(item.expires_at, locale)}
                    </TableCell>
                    <TableCell className="text-right">
                      <InvitationStatusBadge status={item.status} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
