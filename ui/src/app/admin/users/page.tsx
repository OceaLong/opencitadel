"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, MoreHorizontal } from "lucide-react";
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
      toast.success("用户信息已更新");
      setEditing(null);
      await loadUsers(offset);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新失败");
    } finally {
      setSaving(false);
    }
  }

  async function disableUser(user: AdminUser) {
    setSaving(true);
    try {
      await adminApi.patchUser(user.id, { status: "disabled" });
      toast.success("用户已禁用");
      await loadUsers(offset);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "操作失败");
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
      toast.success("配额已更新");
      setQuotaOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存失败");
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
          <h2 className="text-2xl font-semibold tracking-tight">用户管理</h2>
          <p className="text-muted-foreground mt-1 text-sm">查看用户状态、角色与配额</p>
        </div>
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索邮箱 / 用户名"
          className="max-w-xs"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">用户列表</CardTitle>
          <CardDescription>共 {total} 位用户</CardDescription>
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
                      {user.email} · 最近登录 {formatDateTime(user.last_login_at)}
                    </div>
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon">
                        <MoreHorizontal className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => setEditing({ ...user })}>编辑</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => void openQuota(user)}>配额</DropdownMenuItem>
                      {user.status === "active" ? (
                        <DropdownMenuItem variant="destructive" onClick={() => void disableUser(user)}>
                          禁用
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
              第 {currentPage} / {totalPages} 页
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => void loadUsers(Math.max(0, offset - PAGE_SIZE))}>
                上一页
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => void loadUsers(offset + PAGE_SIZE)}
              >
                下一页
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Dialog open={Boolean(editing)} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑用户</DialogTitle>
          </DialogHeader>
          {editing ? (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>显示名称</Label>
                <Input
                  value={editing.display_name}
                  onChange={(event) => setEditing({ ...editing, display_name: event.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>角色</Label>
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
                <Label>状态</Label>
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
              取消
            </Button>
            <Button disabled={saving} onClick={() => void saveUserChanges()}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={quotaOpen} onOpenChange={setQuotaOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>用户配额</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <Field label="Monthly Token Limit" value={quota.monthly_token_limit} onChange={(value) => setQuota({ ...quota, monthly_token_limit: value })} />
            <Field label="Daily Session Limit" value={quota.daily_session_limit} onChange={(value) => setQuota({ ...quota, daily_session_limit: value })} />
            <Field label="Max Concurrent Tasks" value={quota.max_concurrent_tasks} onChange={(value) => setQuota({ ...quota, max_concurrent_tasks: value })} />
            <Field label="Max Storage Bytes" value={quota.max_storage_bytes} onChange={(value) => setQuota({ ...quota, max_storage_bytes: value })} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setQuotaOpen(false)}>
              取消
            </Button>
            <Button disabled={saving} onClick={() => void saveQuota()}>
              保存
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
