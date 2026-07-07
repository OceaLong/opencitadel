"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { LoadingSpinner } from "@/components/loading-spinner";
import { PageHeader } from "@/components/page-header";
import { ScrollablePageContent } from "@/components/scrollable-page-content";
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
import { type Team, teamApi } from "@/lib/api/team";
import { IconAdd, IconLoading, IconUsers } from "@/lib/icons";
import { ACTIVE_WORKSPACE_KEY, LEGACY_ACTIVE_WORKSPACE_KEY } from "@/lib/storage-keys";
import { writeLocalStorageKey } from "@/lib/storage-migration";

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
      const team = await teamApi.create(name.trim(), description.trim());
      toast.success(t("createSuccess"), {
        action: {
          label: t("switchToTeam"),
          onClick: () => {
            writeLocalStorageKey(LEGACY_ACTIVE_WORKSPACE_KEY, ACTIVE_WORKSPACE_KEY, team.id);
            window.location.reload();
          },
        },
      });
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
    <ScrollablePageContent>
      <PageHeader
        bordered={false}
        title={t("title")}
        description={t("subtitle")}
        actions={
          <Button onClick={() => setDialogOpen(true)}>
            <IconAdd className="mr-1 size-4" />
            {t("createTeam")}
          </Button>
        }
      />

      {loading ? (
        <div className="flex justify-center py-16">
          <LoadingSpinner />
        </div>
      ) : teams.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center py-16 text-center">
            <IconUsers className="text-muted-foreground mb-4 size-10" />
            <p className="text-base font-medium">{t("title")}</p>
            <p className="text-muted-foreground mt-1 max-w-sm text-sm">{t("emptyTeams")}</p>
            <Button className="mt-6" onClick={() => setDialogOpen(true)}>
              <IconAdd className="mr-1 size-4" />
              {t("createTeam")}
            </Button>
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
                      <IconUsers className="mr-1 size-4" />
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
              {creating ? <IconLoading className="animate-spin" /> : null}
              {tCommon("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ScrollablePageContent>
  );
}
