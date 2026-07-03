"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus, Users } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import { formatDateTime } from "@/lib/admin-utils";
import { teamApi, type Team } from "@/lib/api/team";

export default function TeamsPage() {
  const t = useTranslations("teams");
  const tCommon = useTranslations("common");
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);

  const loadTeams = useCallback(async () => {
    setLoading(true);
    try {
      const data = await teamApi.list();
      setTeams(data.teams);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : tCommon("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [tCommon]);

  useEffect(() => {
    void loadTeams();
  }, [loadTeams]);

  async function handleCreate() {
    if (!name.trim()) {
      toast.error(t("nameRequired"));
      return;
    }
    setCreating(true);
    try {
      await teamApi.create(name.trim(), description.trim());
      toast.success(t("createSuccess"));
      setDialogOpen(false);
      setName("");
      setDescription("");
      await loadTeams();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("createFailed"));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col gap-6 overflow-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
          <p className="text-muted-foreground mt-1 text-sm">{t("subtitle")}</p>
        </div>
        <Button onClick={() => setDialogOpen(true)}>
          <Plus className="mr-1 size-4" />
          {t("createTeam")}
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="size-6 animate-spin" />
        </div>
      ) : teams.length === 0 ? (
        <Card>
          <CardContent className="text-muted-foreground py-16 text-center text-sm">
            {t("emptyTeams")}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {teams.map((team) => (
            <Card key={team.id} className="transition-colors hover:shadow-[var(--shadow-card-hover)]">
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <CardTitle className="text-base">{team.name}</CardTitle>
                    {team.description ? (
                      <CardDescription className="mt-1">{team.description}</CardDescription>
                    ) : null}
                    <div className="text-muted-foreground mt-2 text-xs">
                      {t("createdAt", { date: formatDateTime(team.created_at) })}
                    </div>
                  </div>
                  <Button variant="outline" size="sm" asChild>
                    <Link href={`/teams/${team.id}`}>
                      <Users className="mr-1 size-4" />
                      {t("manageMembers")}
                    </Link>
                  </Button>
                </div>
              </CardHeader>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("createTeam")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="team-name">{tCommon("name")}</Label>
              <Input
                id="team-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("namePlaceholder")}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="team-description">{t("descriptionLabel")}</Label>
              <Textarea
                id="team-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder={t("descriptionPlaceholder")}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={creating}>
              {tCommon("cancel")}
            </Button>
            <Button onClick={() => void handleCreate()} disabled={creating}>
              {creating ? <Loader2 className="animate-spin" /> : null}
              {tCommon("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
