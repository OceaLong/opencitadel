"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, MoreHorizontal } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

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

import { formatDateTime } from "@/lib/admin-utils";
import { adminApi, type AdminUser, type Quota } from "@/lib/api/admin";

const PAGE_SIZE = 20;

export default function AdminUsersPage() {
  const t = useTranslations("admin");
  const tCommon = useTranslations("common");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [quotaOpen, setQuotaOpen] = useState(false);
  const [quotaUser, setQuotaUser] = useState<AdminUser | null>(null);
  const [quota, setQuota] = useState<Quota>({});
  const [saving, setSaving] = useState(false);

  const loadUsers = useCallback(async (nextOffset: number) => {
    setLoading(true);
    try {
      const data = await adminApi.users({ limit: PAGE_SIZE, offset: nextOffset });
      setUsers(data.users);
      setTotal(data.total);
      setOffset(nextOffset);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadUsers(0);
  }, [loadUsers]);

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

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">{t("usersTitle")}</h2>
          <p className="text-muted-foreground mt-1 text-sm">{t("usersSubtitle")}</p>
        </div>
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t("searchUsersPlaceholder")}
          className="max-w-xs"
        />
      </div>

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
          ) : (
            <div className="space-y-2">
              {filteredUsers.map((user) => (
                <div key={user.id} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{user.display_name || user.username}</span>
                      <Badge variant={user.global_role === "admin" ? "default" : "secondary"}>
                        {user.global_role}
                      </Badge>
                      <Badge variant={user.status === "active" ? "outline" : "destructive"}>{user.status}</Badge>
                    </div>
                    <div className="text-muted-foreground mt-1 text-xs">
                      {user.email} · {t("lastLogin", { time: formatDateTime(user.last_login_at) })}
                    </div>
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon">
                        <MoreHorizontal className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => setEditing({ ...user })}>{tCommon("edit")}</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => void openQuota(user)}>{t("quota")}</DropdownMenuItem>
                      {user.status === "active" ? (
                        <DropdownMenuItem variant="destructive" onClick={() => void disableUser(user)}>
                          {t("disableUser")}
                        </DropdownMenuItem>
                      ) : null}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              ))}
            </div>
          )}

          <div className="mt-4 flex items-center justify-between">
            <span className="text-muted-foreground text-sm">
              {tCommon("pageOf", { current: currentPage, total: totalPages })}
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => void loadUsers(Math.max(0, offset - PAGE_SIZE))}>
                {tCommon("previousPage")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => void loadUsers(offset + PAGE_SIZE)}
              >
                {tCommon("nextPage")}
              </Button>
            </div>
          </div>
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
                  onValueChange={(value: "admin" | "user") => setEditing({ ...editing, global_role: value })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">admin</SelectItem>
                    <SelectItem value="user">user</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>{t("status")}</Label>
                <Select
                  value={editing.status}
                  onValueChange={(value: "active" | "disabled") => setEditing({ ...editing, status: value })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="active">active</SelectItem>
                    <SelectItem value="disabled">disabled</SelectItem>
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
            <Field label={t("quotaMonthlyTokenLimit")} value={quota.monthly_token_limit} onChange={(value) => setQuota({ ...quota, monthly_token_limit: value })} />
            <Field label={t("quotaDailySessionLimit")} value={quota.daily_session_limit} onChange={(value) => setQuota({ ...quota, daily_session_limit: value })} />
            <Field label={t("quotaMaxConcurrentTasks")} value={quota.max_concurrent_tasks} onChange={(value) => setQuota({ ...quota, max_concurrent_tasks: value })} />
            <Field label={t("quotaMaxStorageBytes")} value={quota.max_storage_bytes} onChange={(value) => setQuota({ ...quota, max_storage_bytes: value })} />
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
