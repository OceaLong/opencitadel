"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Loader2, Trash2, Users } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

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
  const [deleteTarget, setDeleteTarget] = useState<AdminTeam | null>(null);

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

  async function confirmDeleteTeam() {
    if (!deleteTarget) return;
    const teamId = deleteTarget.id;
    try {
      await adminApi.deleteTeam(teamId);
      resetWorkspaceIfMatches(teamId);
      toast.success(t("deleteSuccess"));
      if (selectedTeamId === teamId) setSelectedTeamId(null);
      await loadTeams(offset);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("deleteFailed"));
    } finally {
      setDeleteTarget(null);
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

  const selectedTeam = teams.find((team) => team.id === selectedTeamId) ?? null;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("subtitle")} />

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
              <EmptyState title={t("emptyTeams")} className="py-10" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{tCommon("name")}</TableHead>
                    <TableHead className="text-right">{t("columnActions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {teams.map((team) => (
                    <TableRow key={team.id} data-state={selectedTeamId === team.id ? "selected" : undefined}>
                      <TableCell className="max-w-xs min-w-48 whitespace-normal">
                        <button
                          type="button"
                          className="w-full text-left"
                          onClick={() => setSelectedTeamId(team.id)}
                        >
                          <div className="font-medium">{team.name}</div>
                          <div className="text-muted-foreground mt-1 text-xs">
                            {t("memberCount", { count: team.member_count })} · {formatDateTime(team.created_at)}
                          </div>
                        </button>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="icon-sm" onClick={() => setDeleteTarget(team)}>
                          <Trash2 className="text-destructive size-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            <div className="flex items-center justify-between pt-2">
              <span className="text-muted-foreground text-sm">
                {tCommon("pageOf", { current: currentPage, total: totalPages })}
              </span>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => void loadTeams(Math.max(0, offset - PAGE_SIZE))}>
                  {tCommon("previousPage")}
                </Button>
                <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= total} onClick={() => void loadTeams(offset + PAGE_SIZE)}>
                  {tCommon("nextPage")}
                </Button>
              </div>
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
              <EmptyState title={t("selectTeamHint")} className="py-10" />
            ) : membersLoading ? (
              <div className="flex justify-center py-10">
                <Loader2 className="size-6 animate-spin" />
              </div>
            ) : members.length === 0 ? (
              <EmptyState title={t("emptyMembers")} className="py-10" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("columnMember")}</TableHead>
                    <TableHead>{t("columnRole")}</TableHead>
                    <TableHead className="text-right">{t("columnActions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {members.map((member) => (
                    <TableRow key={member.user_id}>
                      <TableCell className="max-w-56 min-w-40 whitespace-normal">
                        <div className="truncate font-medium">{memberDisplayName(member)}</div>
                        {member.email && member.display_name ? (
                          <div className="text-muted-foreground truncate text-xs">{member.email}</div>
                        ) : null}
                        <div className="text-muted-foreground text-xs">{formatDateTime(member.joined_at)}</div>
                      </TableCell>
                      <TableCell>
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
                      </TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="icon-sm" onClick={() => void handleRemoveMember(member.user_id)}>
                          <Trash2 className="text-destructive size-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      <ConfirmDeleteDialog
        open={deleteTarget != null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        onConfirm={confirmDeleteTeam}
        title={t("deleteConfirm")}
        description={t("deleteConfirmDesc", { name: deleteTarget?.name ?? "" })}
      />
    </div>
  );
}
