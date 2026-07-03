"use client";

import { useCallback, useRef, useState } from "react";
import { FileUp, Globe, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { inferSourceType } from "@/components/knowledge/knowledge-utils";
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

import { fileApi } from "@/lib/api/file";
import { knowledgeApi } from "@/lib/api/knowledge";
import type { KnowledgeBase, KnowledgeSourceType } from "@/lib/api/types";

type AddDocumentDialogProps = {
  kbId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdded: (kb: KnowledgeBase) => void;
};

type UploadedFile = {
  id: string;
  sourceType: KnowledgeSourceType;
};

export function AddDocumentDialog({ kbId, open, onOpenChange, onAdded }: AddDocumentDialogProps) {
  const t = useTranslations("knowledge.addDialog");
  const tCommon = useTranslations("common");
  const tChatInput = useTranslations("chatInput");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<"files" | "web">("files");
  const [uploading, setUploading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
  const [url, setUrl] = useState("");

  const resetForm = useCallback(() => {
    setUploadedFiles([]);
    setUrl("");
    setTab("files");
  }, []);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) resetForm();
      onOpenChange(next);
    },
    [onOpenChange, resetForm],
  );

  const handleFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files;
    if (!selected?.length) return;
    setUploading(true);
    try {
      const next: UploadedFile[] = [];
      for (const file of Array.from(selected)) {
        const info = await fileApi.uploadFile({ file });
        next.push({ id: info.id, sourceType: inferSourceType(file.name) });
      }
      setUploadedFiles((prev) => [...prev, ...next]);
      toast.success(tChatInput("uploadSuccess", { count: next.length }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : tCommon("uploadFailed"));
    } finally {
      setUploading(false);
    }
  };

  const handleAdd = useCallback(async () => {
    setAdding(true);
    try {
      let kb: KnowledgeBase;
      if (tab === "web") {
        if (!url.trim()) {
          toast.error(t("urlRequired"));
          return;
        }
        kb = await knowledgeApi.addDocuments(kbId, {
          urls: [url.trim()],
          source_type: "web",
        });
      } else {
        if (!uploadedFiles.length) {
          toast.error(t("filesRequired"));
          return;
        }
        kb = await knowledgeApi.addDocuments(kbId, {
          file_ids: uploadedFiles.map((item) => item.id),
          source_type: "upload",
        });
      }
      onAdded(kb);
      handleOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("addFailed"));
    } finally {
      setAdding(false);
    }
  }, [handleOpenChange, kbId, onAdded, tab, uploadedFiles, url, t]);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        <Tabs value={tab} onValueChange={(value) => setTab(value as "files" | "web")}>
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="files">{t("tabFiles")}</TabsTrigger>
            <TabsTrigger value="web">{t("tabWeb")}</TabsTrigger>
          </TabsList>
          <TabsContent value="files" className="space-y-3 pt-3">
            <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleFiles} />
            <Button
              variant="outline"
              className="w-full"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
            >
              {uploading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <FileUp className="mr-2 size-4" />}
              {t("selectFiles", { count: uploadedFiles.length })}
            </Button>
          </TabsContent>
          <TabsContent value="web" className="space-y-3 pt-3">
            <div className="space-y-2">
              <Label htmlFor="kb-url">{t("webUrlLabel")}</Label>
              <div className="flex items-center gap-2">
                <Globe className="text-muted-foreground size-4" />
                <Input id="kb-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." />
              </div>
            </div>
          </TabsContent>
        </Tabs>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button onClick={handleAdd} disabled={adding || uploading}>
            {adding && <Loader2 className="mr-2 size-4 animate-spin" />}
            {t("addAndIndex")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
