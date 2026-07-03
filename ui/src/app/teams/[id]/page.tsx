"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { Copy, Loader2, LogOut, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { teamApi, memberDisplayName, type Team, type TeamMember, type TeamMemberDetail } from "@/lib/api/team";
import { resetWorkspaceIfMatches } from "@/lib/workspace-utils";
import { useAuth } from "@/providers/auth-provider";

export default function TeamDetailPage() {
  const params = useParams<{ id: string }>();
  const teamId = params.id;
  const { user } = useAuth();
  const t = useTranslations("teams");
  const tCommon = useTranslations("common");
  const [team, setTeam] = useState<Team | null>(null);
  const [members, setMembers] = useState<TeamMemberDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [inviteRole, setInviteRole] = useState<TeamMember["role"]>("member");
  const [inviting, setInviting] = useState(false);
  const [inviteUrl, setInviteUrl] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [leaving, setLeaving] = useState(false);

  const myRole = useMemo(
    () => members.find((member) => member.user_id === user?.id)?.role,
    [members, user?.id],
  );
  const isOwner = myRole === "owner";
  const canManageMembers = myRole === "owner" || myRole === "admin";
  const ownerCount = useMemo(() => members.filter((member) => member.role === "owner").length, [members]);
  const isSoleOwner = isOwner && ownerCount <= 1;

  function roleLabel(role: TeamMember["role"]) {
    if (role === "owner") return t("roleOwner");
    if (role === "admin") return t("roleAdmin");
    return t("roleMember");
  }

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [teamData, membersData] = await Promise.all([
        teamApi.get(teamId),
        teamApi.members(teamId),
      ]);
      setTeam(teamData);
      setMembers(membersData.members);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : tCommon("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [teamId, tCommon]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  async function handleInvite() {
    setInviting(true);
    try {
      const data = await teamApi.invite(teamId, inviteRole);
      setInviteUrl(data.url);
      toast.success(t("inviteGenerated"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("inviteFailed"));
    } finally {
      setInviting(false);
    }
  }

  async function copyInviteUrl() {
    if (!inviteUrl) return;
    await navigator.clipboard.writeText(inviteUrl);
    toast.success(tCommon("copy"));
  }

  async function handleRemoveMember(userId: string) {
    try {
      await teamApi.removeMember(teamId, userId);
      toast.success(t("memberRemoved"));
      await loadData();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("memberRemoveFailed"));
    }
  }

  async function handleRoleChange(userId: string, role: TeamMember["role"]) {
    try {
      await teamApi.updateMemberRole(teamId, userId, role);
      toast.success(t("roleUpdated"));
      await loadData();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("roleUpdateFailed"));
    }
  }

  async function handleDeleteTeam() {
    if (!window.confirm(t("deleteConfirm"))) return;
    setDeleting(true);
    try {
      await teamApi.remove(teamId);
      resetWorkspaceIfMatches(teamId);
      toast.success(t("deleteSuccess"));
      window.location.href = "/teams";
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("deleteFailed"));
    } finally {
      setDeleting(false);
    }
  }

  async function handleLeaveTeam() {
    if (!window.confirm(t("leaveConfirm"))) return;
    setLeaving(true);
    try {
      await teamApi.leave(teamId);
      resetWorkspaceIfMatches(teamId);
      toast.success(t("leaveSuccess"));
      window.location.href = "/teams";
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("leaveFailed"));
    } finally {
      setLeaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );
  }

  if (!team) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-sm">
        <p className="text-muted-foreground">{t("teamNotFound")}</p>
        <Button variant="outline" asChild>
          <Link href="/teams">{t("backToTeams")}</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col gap-6 overflow-auto p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Button variant="ghost" size="sm" asChild className="mb-2 px-0">
            <Link href="/teams">{t("backToTeams")}</Link>
          </Button>
          <h1 className="text-2xl font-semibold tracking-tight">{team.name}</h1>
          {team.description ? (
            <p className="text-muted-foreground mt-1 text-sm">{team.description}</p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {myRole ? (
            <Button
              variant="outline"
              onClick={() => void handleLeaveTeam()}
              disabled={leaving || isSoleOwner}
              title={isSoleOwner ? t("soleOwnerCannotLeave") : undefined}
            >
              {leaving ? <Loader2 className="animate-spin" /> : <LogOut className="mr-1 size-4" />}
              {t("leaveTeam")}
            </Button>
          ) : null}
          {isOwner ? (
            <Button variant="destructive" onClick={() => void handleDeleteTeam()} disabled={deleting}>
              {deleting ? <Loader2 className="animate-spin" /> : <Trash2 className="mr-1 size-4" />}
              {t("deleteTeam")}
            </Button>
          ) : null}
        </div>
      </div>

      {canManageMembers ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("inviteMember")}</CardTitle>
            <CardDescription>{t("inviteDescription")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Select value={inviteRole} onValueChange={(value) => setInviteRole(value as TeamMember["role"])}>
                <SelectTrigger className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="member">{t("roleMember")}</SelectItem>
                  <SelectItem value="admin">{t("roleAdmin")}</SelectItem>
                  <SelectItem value="owner">{t("roleOwner")}</SelectItem>
                </SelectContent>
              </Select>
              <Button onClick={() => void handleInvite()} disabled={inviting}>
                {inviting ? <Loader2 className="animate-spin" /> : null}
                {t("generateInviteLink")}
              </Button>
            </div>
            {inviteUrl ? (
              <div className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{inviteUrl}</span>
                <Button variant="ghost" size="icon-sm" onClick={() => void copyInviteUrl()}>
                  <Copy className="size-4" />
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("membersTitle")}</CardTitle>
          <CardDescription>{t("membersDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {members.map((member) => (
            <div
              key={member.user_id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm"
            >
              <div className="min-w-0">
                <div className="truncate font-medium">
                  {memberDisplayName(member)}
                  {member.user_id === user?.id ? (
                    <span className="text-muted-foreground ml-1 text-xs">({t("you")})</span>
                  ) : null}
                </div>
                {member.email && member.display_name ? (
                  <div className="text-muted-foreground truncate text-xs">{member.email}</div>
                ) : null}
                <div className="text-muted-foreground text-xs">
                  {t("joinedAt", { date: new Date(member.joined_at).toLocaleString() })}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {isOwner && member.user_id !== user?.id ? (
                  <Select
                    value={member.role}
                    onValueChange={(value) => void handleRoleChange(member.user_id, value as TeamMember["role"])}
                  >
                    <SelectTrigger className="w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="member">{t("roleMember")}</SelectItem>
                      <SelectItem value="admin">{t("roleAdmin")}</SelectItem>
                      <SelectItem value="owner">{t("roleOwner")}</SelectItem>
                    </SelectContent>
                  </Select>
                ) : (
                  <Badge variant="secondary">{roleLabel(member.role)}</Badge>
                )}
                {canManageMembers && member.user_id !== user?.id ? (
                  <Button variant="ghost" size="icon-sm" onClick={() => void handleRemoveMember(member.user_id)}>
                    <Trash2 className="text-destructive size-4" />
                  </Button>
                ) : null}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
