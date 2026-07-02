"use client";

import { useEffect, useState } from "react";
import { Copy, Loader2, MailPlus } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

import { formatDateTime } from "@/lib/admin-utils";
import { adminApi, type PlatformInvitation } from "@/lib/api/admin";

export default function AdminInvitationsPage() {
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
      toast.error("请输入邮箱");
      return;
    }
    setCreating(true);
    try {
      const data = await adminApi.invite(inviteEmail.trim());
      setInviteUrl(data.url);
      toast.success("邀请链接已生成");
      setInviteEmail("");
      await loadInvitations();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "生成失败");
    } finally {
      setCreating(false);
    }
  }

  async function copyUrl(url: string) {
    await navigator.clipboard.writeText(url);
    toast.success("已复制邀请链接");
  }

  const pending = invitations.filter((item) => item.status === "pending").length;
  const accepted = invitations.filter((item) => item.status === "accepted").length;
  const expired = invitations.filter((item) => item.status === "expired").length;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">平台邀请</h2>
        <p className="text-muted-foreground mt-1 text-sm">创建邀请链接并跟踪注册转化</p>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <SummaryCard label="待注册" value={pending} />
        <SummaryCard label="已接受" value={accepted} />
        <SummaryCard label="已过期" value={expired} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">生成邀请</CardTitle>
          <CardDescription>邀请链接有效期 7 天</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={inviteEmail}
              onChange={(event) => setInviteEmail(event.target.value)}
              placeholder="user@example.com"
            />
            <Button disabled={creating} onClick={() => void createInvite()}>
              {creating ? <Loader2 className="mr-1 size-4 animate-spin" /> : <MailPlus className="mr-1 size-4" />}
              生成邀请链接
            </Button>
          </div>
          {inviteUrl ? (
            <div className="bg-muted/30 flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
              <span className="truncate">{inviteUrl}</span>
              <Button variant="ghost" size="icon" onClick={() => void copyUrl(inviteUrl)}>
                <Copy className="size-4" />
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">邀请记录</CardTitle>
          <CardDescription>共 {total} 条记录</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="size-6 animate-spin" />
            </div>
          ) : invitations.length === 0 ? (
            <div className="text-muted-foreground py-10 text-center text-sm">暂无邀请记录</div>
          ) : (
            <div className="space-y-2">
              {invitations.map((item) => (
                <div key={item.id} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-3">
                  <div className="min-w-0">
                    <div className="truncate font-medium">{item.email || "未指定邮箱"}</div>
                    <div className="text-muted-foreground mt-1 text-xs">
                      创建于 {formatDateTime(item.created_at)} · 过期 {formatDateTime(item.expires_at)}
                    </div>
                  </div>
                  <StatusBadge status={item.status} />
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SummaryCard({ label, value }: { label: string; value: number }) {
  return (
    <Card className="gap-0 py-4">
      <CardContent>
        <div className="text-muted-foreground text-sm">{label}</div>
        <div className="mt-1 text-2xl font-semibold">{value}</div>
      </CardContent>
    </Card>
  );
}

function StatusBadge({ status }: { status: PlatformInvitation["status"] }) {
  const label = status === "accepted" ? "已接受" : status === "pending" ? "待注册" : "已过期";
  const variant = status === "accepted" ? "secondary" : status === "pending" ? "outline" : "destructive";
  return <Badge variant={variant}>{label}</Badge>;
}
