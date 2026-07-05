"use client";

import { useCallback, useRef, useState } from "react";
import { FolderArchive, GitBranch, Loader2, Upload } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { codebaseApi } from "@/lib/api/codebase";
import { fileApi } from "@/lib/api/file";
import { CODEBASE_ZIP_MAX_BYTES } from "@/lib/constants";
import type { Codebase, CodebaseSourceType } from "@/lib/api/types";

type CreateCodebaseDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (codebase: Codebase) => void;
};

export function CreateCodebaseDialog({
  open,
  onOpenChange,
  onCreated,
}: CreateCodebaseDialogProps) {
  const t = useTranslations("codebase.createDialog");
  const tCommon = useTranslations("common");
  const tChatInput = useTranslations("chatInput");
  const [name, setName] = useState("");
  const [gitUrl, setGitUrl] = useState("");
  const [uploading, setUploading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [tab, setTab] = useState<CodebaseSourceType>("zip");
  const zipInputRef = useRef<HTMLInputElement>(null);
  const filesInputRef = useRef<HTMLInputElement>(null);
  const [uploadedZipId, setUploadedZipId] = useState<string | null>(null);
  const [uploadedFileIds, setUploadedFileIds] = useState<string[]>([]);

  const handleZipUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > CODEBASE_ZIP_MAX_BYTES) {
      toast.error(t("fileTooLarge200mb"));
      e.target.value = "";
      return;
    }
    setUploading(true);
    try {
      const info = await fileApi.uploadFile({ file });
      setUploadedZipId(info.id);
      if (!name) setName(file.name.replace(/\.(zip|tar\.gz|tgz)$/i, ""));
      toast.success(t("zipUploadSuccess"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : tCommon("uploadFailed"));
    } finally {
      setUploading(false);
    }
  };

  const handleFilesUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files;
    if (!selected?.length) return;
    setUploading(true);
    try {
      const ids: string[] = [];
      for (const file of Array.from(selected)) {
        const info = await fileApi.uploadFile({ file });
        ids.push(info.id);
      }
      setUploadedFileIds((prev) => [...prev, ...ids]);
      toast.success(tChatInput("uploadSuccess", { count: ids.length }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : tCommon("uploadFailed"));
    } finally {
      setUploading(false);
    }
  };

  const handleCreate = useCallback(async () => {
    setCreating(true);
    try {
      let codebase: Codebase;
      if (tab === "git") {
        if (!gitUrl.trim()) {
          toast.error(t("gitUrlRequired"));
          return;
        }
        codebase = await codebaseApi.create({
          name: name || t("defaultGitName"),
          source_type: "git",
          git_url: gitUrl.trim(),
        });
      } else if (tab === "zip") {
        if (!uploadedZipId) {
          toast.error(t("zipRequired"));
          return;
        }
        codebase = await codebaseApi.create({
          name: name || t("defaultZipName"),
          source_type: "zip",
          file_id: uploadedZipId,
        });
      } else {
        if (!uploadedFileIds.length) {
          toast.error(t("filesRequired"));
          return;
        }
        codebase = await codebaseApi.create({
          name: name || t("defaultFilesName"),
          source_type: "files",
          file_ids: uploadedFileIds,
        });
      }
      onCreated(codebase);
      onOpenChange(false);
      setName("");
      setGitUrl("");
      setUploadedZipId(null);
      setUploadedFileIds([]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("createFailed"));
    } finally {
      setCreating(false);
    }
  }, [tab, name, gitUrl, uploadedZipId, uploadedFileIds, onCreated, onOpenChange, t]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="cb-name">{tCommon("name")}</Label>
            <Input
              id="cb-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("namePlaceholder")}
            />
          </div>
          <Tabs value={tab} onValueChange={(v) => setTab(v as CodebaseSourceType)}>
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="zip">{t("tabZip")}</TabsTrigger>
              <TabsTrigger value="git">{t("tabGit")}</TabsTrigger>
              <TabsTrigger value="files">{t("tabFiles")}</TabsTrigger>
            </TabsList>
            <TabsContent value="zip" className="space-y-3 pt-2">
              <input
                ref={zipInputRef}
                type="file"
                accept=".zip,.tar.gz,.tgz"
                className="hidden"
                onChange={handleZipUpload}
              />
              <Button
                variant="outline"
                className="w-full"
                onClick={() => zipInputRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : (
                  <FolderArchive className="mr-2 size-4" />
                )}
                {uploadedZipId ? t("zipUploaded") : t("selectZip")}
              </Button>
            </TabsContent>
            <TabsContent value="git" className="space-y-3 pt-2">
              <div className="flex items-center gap-2">
                <GitBranch className="text-muted-foreground size-4" />
                <Input
                  value={gitUrl}
                  onChange={(e) => setGitUrl(e.target.value)}
                  placeholder="https://github.com/org/repo.git"
                />
              </div>
            </TabsContent>
            <TabsContent value="files" className="space-y-3 pt-2">
              <input
                ref={filesInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={handleFilesUpload}
              />
              <Button
                variant="outline"
                className="w-full"
                onClick={() => filesInputRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : (
                  <Upload className="mr-2 size-4" />
                )}
                {t("selectFiles", { count: uploadedFileIds.length })}
              </Button>
            </TabsContent>
          </Tabs>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button onClick={handleCreate} disabled={creating || uploading}>
            {creating && <Loader2 className="mr-2 size-4 animate-spin" />}
            {t("createAndAnalyze")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
