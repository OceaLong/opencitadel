"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Trash2, Users } from "lucide-react";
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

import { formatDateTime } from "@/lib/admin-utils";
import { adminApi, type AdminTeam } from "@/lib/api/admin";
import { memberDisplayName, type TeamMember, type TeamMemberDetail } from "@/lib/api/team";
import { resetWorkspaceIfMatches } from "@/lib/workspace-utils";

const PAGE_SIZE = 20;

export default function AdminTeamsPage() {
  const t = useTranslations("adminTeams");
  const tCommon = useTranslations("common");
  const [teams, setTeams] = useState<AdminTeam[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [members, setMembers] = useState<TeamMemberDetail[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);

  const loadTeams = useCallback(async (nextOffset: number) => {
    setLoading(true);
    try {
      const data = await adminApi.teams({ limit: PAGE_SIZE, offset: nextOffset });
      setTeams(data.teams);
      setTotal(data.total);
      setOffset(nextOffset);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : tCommon("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [tCommon]);

  const loadMembers = useCallback(async (teamId: string) => {
    setMembersLoading(true);
    try {
      const data = await adminApi.teamMembers(teamId);
      setMembers(data.members);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : tCommon("loadFailed"));
      setMembers([]);
    } finally {
      setMembersLoading(false);
    }
  }, [tCommon]);

  useEffect(() => {
    void loadTeams(0);
  }, [loadTeams]);

  useEffect(() => {
    if (!selectedTeamId) {
      setMembers([]);
      return;
    }
    void loadMembers(selectedTeamId);
  }, [loadMembers, selectedTeamId]);

  async function handleDeleteTeam(teamId: string) {
    if (!window.confirm(t("deleteConfirm"))) return;
    try {
      await adminApi.deleteTeam(teamId);
      resetWorkspaceIfMatches(teamId);
      toast.success(t("deleteSuccess"));
      if (selectedTeamId === teamId) setSelectedTeamId(null);
      await loadTeams(offset);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("deleteFailed"));
    }
  }

  async function handleRemoveMember(userId: string) {
    if (!selectedTeamId) return;
    try {
      await adminApi.removeTeamMember(selectedTeamId, userId);
      toast.success(t("memberRemoved"));
      await loadMembers(selectedTeamId);
      await loadTeams(offset);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("memberRemoveFailed"));
    }
  }

  async function handleRoleChange(userId: string, role: TeamMember["role"]) {
    if (!selectedTeamId) return;
    try {
      await adminApi.updateTeamMemberRole(selectedTeamId, userId, role);
      toast.success(t("roleUpdated"));
      await loadMembers(selectedTeamId);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("roleUpdateFailed"));
    }
  }

  function roleLabel(role: TeamMember["role"]) {
    if (role === "owner") return t("roleOwner");
    if (role === "admin") return t("roleAdmin");
    return t("roleMember");
  }

  const selectedTeam = teams.find((team) => team.id === selectedTeamId) ?? null;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">{t("title")}</h2>
        <p className="text-muted-foreground mt-1 text-sm">{t("subtitle")}</p>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("teamsListTitle")}</CardTitle>
            <CardDescription>{t("teamsListDesc", { total })}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {loading ? (
              <div className="flex justify-center py-10">
                <Loader2 className="size-6 animate-spin" />
              </div>
            ) : teams.length === 0 ? (
              <div className="text-muted-foreground py-10 text-center text-sm">{t("emptyTeams")}</div>
            ) : (
              teams.map((team) => (
                <div
                  key={team.id}
                  className={`rounded-lg border px-3 py-3 text-sm ${selectedTeamId === team.id ? "border-primary bg-primary/5" : ""}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      onClick={() => setSelectedTeamId(team.id)}
                    >
                      <div className="font-medium">{team.name}</div>
                      <div className="text-muted-foreground mt-1 text-xs">
                        {t("memberCount", { count: team.member_count })} · {formatDateTime(team.created_at)}
                      </div>
                    </button>
                    <Button variant="ghost" size="icon-sm" onClick={() => void handleDeleteTeam(team.id)}>
                      <Trash2 className="text-destructive size-4" />
                    </Button>
                  </div>
                </div>
              ))
            )}
            <div className="flex items-center justify-between pt-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => void loadTeams(Math.max(0, offset - PAGE_SIZE))}>
                {tCommon("back")}
              </Button>
              <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= total} onClick={() => void loadTeams(offset + PAGE_SIZE)}>
                {t("nextPage")}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Users className="size-4" />
              {t("membersTitle")}
            </CardTitle>
            <CardDescription>
              {selectedTeam ? selectedTeam.name : t("selectTeamHint")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {!selectedTeam ? (
              <div className="text-muted-foreground py-10 text-center text-sm">{t("selectTeamHint")}</div>
            ) : membersLoading ? (
              <div className="flex justify-center py-10">
                <Loader2 className="size-6 animate-spin" />
              </div>
            ) : members.length === 0 ? (
              <div className="text-muted-foreground py-10 text-center text-sm">{t("emptyMembers")}</div>
            ) : (
              members.map((member) => (
                <div key={member.user_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
                  <div className="min-w-0">
                    <div className="truncate font-medium">{memberDisplayName(member)}</div>
                    {member.email && member.display_name ? (
                      <div className="text-muted-foreground truncate text-xs">{member.email}</div>
                    ) : null}
                    <div className="text-muted-foreground text-xs">{formatDateTime(member.joined_at)}</div>
                  </div>
                  <div className="flex items-center gap-2">
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
                    <Badge variant="secondary">{roleLabel(member.role)}</Badge>
                    <Button variant="ghost" size="icon-sm" onClick={() => void handleRemoveMember(member.user_id)}>
                      <Trash2 className="text-destructive size-4" />
                    </Button>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
