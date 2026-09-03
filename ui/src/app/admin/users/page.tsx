"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Loader2, MoreHorizontal } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

import { type PaginatedFetcher, usePaginatedList } from "@/hooks/use-paginated-list";
import { formatDateTime } from "@/lib/admin-utils";
import { adminApi, type AdminTeam, type AdminUser, type Quota } from "@/lib/api/admin";

export default function AdminUsersPage() {
  const t = useTranslations("admin");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [quotaOpen, setQuotaOpen] = useState(false);
  const [quotaUser, setQuotaUser] = useState<AdminUser | null>(null);
  const [quota, setQuota] = useState<Quota>({});
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null);
  const [deleteStrategy, setDeleteStrategy] = useState<
    "anonymize" | "cascade" | "transfer_to_team"
  >("anonymize");
  const [deleting, setDeleting] = useState(false);
  const [teams, setTeams] = useState<AdminTeam[]>([]);
  const [teamsLoading, setTeamsLoading] = useState(false);
  const [transferTeamId, setTransferTeamId] = useState<string>("");

  const fetchUsers = useCallback<PaginatedFetcher<AdminUser>>(
    async ({ limit, offset }) => {
      try {
        const data = await adminApi.users({ limit, offset });
        return { items: data.users, total: data.total };
      } catch (error) {
        toast.error(error instanceof Error ? error.message : tCommon("loadFailed"));
        return null;
      }
    },
    [tCommon],
  );

  const {
    items: users,
    total,
    offset,
    loading,
    totalPages,
    currentPage,
    canPrev,
    canNext,
    load: loadUsers,
    nextPage,
    prevPage,
  } = usePaginatedList<AdminUser>(fetchUsers);

  useEffect(() => {
    void loadUsers(0);
  }, [loadUsers]);

  // 选择 "转移给团队" 策略时懒加载可选团队列表(选择器必选,避免提交缺 team_id 被后端 400)。
  useEffect(() => {
    if (
      !deleteTarget ||
      deleteStrategy !== "transfer_to_team" ||
      teams.length > 0 ||
      teamsLoading
    ) {
      return;
    }
    let cancelled = false;
    setTeamsLoading(true);
    void adminApi
      .teams({ limit: 100 })
      .then((data) => {
        if (!cancelled) setTeams(data.teams);
      })
      .catch(() => {
        if (!cancelled) setTeams([]);
      })
      .finally(() => {
        if (!cancelled) setTeamsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [deleteTarget, deleteStrategy, teams.length, teamsLoading]);

  const filteredUsers = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return users;
    return users.filter(
      (user) =>
        user.email.toLowerCase().includes(keyword) ||
        user.username.toLowerCase().includes(keyword) ||
        user.display_name.toLowerCase().includes(keyword),
    );
  }, [search, users]);

  async function saveUserChanges() {
    if (!editing) return;
    setSaving(true);
    try {
      await adminApi.patchUser(editing.id, {
        global_role: editing.global_role,
        status: editing.status,
        display_name: editing.display_name,
      });
      toast.success(t("userUpdated"));
      setEditing(null);
      await loadUsers(offset);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("updateFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function disableUser(user: AdminUser) {
    setSaving(true);
    try {
      await adminApi.patchUser(user.id, { status: "disabled" });
      toast.success(t("userDisabled"));
      await loadUsers(offset);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("operationFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function openQuota(user: AdminUser) {
    setQuotaUser(user);
    setQuotaOpen(true);
    try {
      const data = await adminApi.getQuota(user.id);
      setQuota(data);
    } catch {
      setQuota({});
    }
  }

  async function saveQuota() {
    if (!quotaUser) return;
    setSaving(true);
    try {
      await adminApi.putQuota(quotaUser.id, quota);
      toast.success(t("quotaUpdated"));
      setQuotaOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : tCommon("saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function deleteUser() {
    if (!deleteTarget) return;
    if (deleteStrategy === "transfer_to_team" && !transferTeamId) return;
    setDeleting(true);
    try {
      await adminApi.deleteUser(
        deleteTarget.id,
        deleteStrategy,
        deleteStrategy === "transfer_to_team" ? transferTeamId : undefined,
      );
      toast.success(t("userDeleted"));
      setDeleteTarget(null);
      await loadUsers(offset);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("deleteUserFailed"));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("usersTitle")}
        description={t("usersSubtitle")}
        actions={
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("searchUsersPlaceholder")}
            className="max-w-xs"
          />
        }
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("userListTitle")}</CardTitle>
          <CardDescription>{t("userTotalCount", { count: total })}</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="size-6 animate-spin" />
            </div>
          ) : filteredUsers.length === 0 ? (
            <EmptyState
              title={t("noUsersFound")}
              description={search.trim() ? t("searchCurrentPageHint") : undefined}
              className="py-10"
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("columnUser")}</TableHead>
                  <TableHead>{t("role")}</TableHead>
                  <TableHead>{t("status")}</TableHead>
                  <TableHead>{t("columnLastLogin")}</TableHead>
                  <TableHead className="text-right">{t("columnActions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredUsers.map((user) => (
                  <TableRow key={user.id}>
                    <TableCell className="max-w-xs min-w-48 whitespace-normal">
                      <div className="font-medium">{user.display_name || user.username}</div>
                      <div className="text-muted-foreground mt-0.5 text-xs">{user.email}</div>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          user.global_role === "admin"
                            ? "default"
                            : user.global_role === "auditor"
                              ? "outline"
                              : "secondary"
                        }
                      >
                        {user.global_role}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={user.status === "active" ? "outline" : "destructive"}>
                        {user.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground font-mono text-xs">
                      {formatDateTime(user.last_login_at, locale)}
                    </TableCell>
                    <TableCell className="text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon">
                            <MoreHorizontal className="size-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => setEditing({ ...user })}>
                            {tCommon("edit")}
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => void openQuota(user)}>
                            {t("quota")}
                          </DropdownMenuItem>
                          {user.status === "active" ? (
                            <DropdownMenuItem
                              variant="destructive"
                              onClick={() => void disableUser(user)}
                            >
                              {t("disableUser")}
                            </DropdownMenuItem>
                          ) : null}
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => setDeleteTarget(user)}
                          >
                            {t("deleteUser")}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {search.trim() ? null : (
            <div className="mt-4 flex items-center justify-between">
              <span className="text-muted-foreground text-sm">
                {tCommon("pageOf", { current: currentPage, total: totalPages })}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!canPrev}
                  onClick={() => void prevPage()}
                >
                  {tCommon("previousPage")}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!canNext}
                  onClick={() => void nextPage()}
                >
                  {tCommon("nextPage")}
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={Boolean(editing)} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("editUser")}</DialogTitle>
          </DialogHeader>
          {editing ? (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>{t("displayName")}</Label>
                <Input
                  value={editing.display_name}
                  onChange={(event) => setEditing({ ...editing, display_name: event.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t("role")}</Label>
                <Select
                  value={editing.global_role}
                  onValueChange={(value: "admin" | "user" | "auditor") =>
                    setEditing({ ...editing, global_role: value })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">{t("roleAdminEnum")}</SelectItem>
                    <SelectItem value="user">{t("roleUser")}</SelectItem>
                    <SelectItem value="auditor">{t("roleAuditor")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>{t("status")}</Label>
                <Select
                  value={editing.status}
                  onValueChange={(value: "active" | "disabled") =>
                    setEditing({ ...editing, status: value })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="active">{t("statusActive")}</SelectItem>
                    <SelectItem value="disabled">{t("statusDisabled")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>
              {tCommon("cancel")}
            </Button>
            <Button disabled={saving} onClick={() => void saveUserChanges()}>
              {tCommon("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={quotaOpen} onOpenChange={setQuotaOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("userQuota")}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <Field
              label={t("quotaMonthlyTokenLimit")}
              value={quota.monthly_token_limit}
              onChange={(value) => setQuota({ ...quota, monthly_token_limit: value })}
            />
            <Field
              label={t("quotaDailySessionLimit")}
              value={quota.daily_session_limit}
              onChange={(value) => setQuota({ ...quota, daily_session_limit: value })}
            />
            <Field
              label={t("quotaMaxConcurrentTasks")}
              value={quota.max_concurrent_tasks}
              onChange={(value) => setQuota({ ...quota, max_concurrent_tasks: value })}
            />
            <Field
              label={t("quotaMaxStorageBytes")}
              value={quota.max_storage_bytes}
              onChange={(value) => setQuota({ ...quota, max_storage_bytes: value })}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setQuotaOpen(false)}>
              {tCommon("cancel")}
            </Button>
            <Button disabled={saving} onClick={() => void saveQuota()}>
              {tCommon("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null);
            setDeleteStrategy("anonymize");
            setTransferTeamId("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("deleteUserTitle")}</DialogTitle>
          </DialogHeader>
          {deleteTarget ? (
            <div className="space-y-4">
              <p className="text-muted-foreground text-sm">
                {t("deleteUserDesc", { name: deleteTarget.display_name || deleteTarget.username })}
              </p>
              <div className="space-y-2">
                <Label>{t("deleteStrategy")}</Label>
                <Select
                  value={deleteStrategy}
                  onValueChange={(value: "anonymize" | "cascade" | "transfer_to_team") => {
                    setDeleteStrategy(value);
                    if (value !== "transfer_to_team") setTransferTeamId("");
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="anonymize">{t("deleteStrategyAnonymize")}</SelectItem>
                    <SelectItem value="cascade">{t("deleteStrategyCascade")}</SelectItem>
                    <SelectItem value="transfer_to_team">{t("deleteStrategyTransfer")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {deleteStrategy === "transfer_to_team" ? (
                <div className="space-y-2">
                  <Label>{t("transferTargetTeam")}</Label>
                  <Select
                    value={transferTeamId || undefined}
                    onValueChange={setTransferTeamId}
                    disabled={teamsLoading}
                  >
                    <SelectTrigger>
                      <SelectValue
                        placeholder={teamsLoading ? tCommon("loading") : t("selectTeamPlaceholder")}
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {teams.map((team) => (
                        <SelectItem key={team.id} value={team.id}>
                          {team.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {!teamsLoading && teams.length === 0 ? (
                    <p className="text-muted-foreground text-xs">{t("noTeamsAvailable")}</p>
                  ) : !transferTeamId ? (
                    <p className="text-destructive text-xs">{t("transferTeamRequired")}</p>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setDeleteTarget(null);
                setDeleteStrategy("anonymize");
                setTransferTeamId("");
              }}
            >
              {tCommon("cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={deleting || (deleteStrategy === "transfer_to_team" && !transferTeamId)}
              onClick={() => void deleteUser()}
            >
              {deleting ? tCommon("deleting") : t("deleteUser")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
}: {
  label: string;
  value?: number | null;
  onChange: (value: number | null) => void;
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <Input
        type="number"
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
      />
    </div>
  );
}
