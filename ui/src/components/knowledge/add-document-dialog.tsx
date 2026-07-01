"use client";

import { useCallback, useRef, useState } from "react";
import { FileUp, Globe2, Loader2 } from "lucide-react";
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
      toast.success(`已上传 ${next.length} 个文件`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "上传失败");
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
          toast.error("请输入网页 URL");
          return;
        }
        kb = await knowledgeApi.addDocuments(kbId, {
          urls: [url.trim()],
          source_type: "web",
        });
      } else {
        if (!uploadedFiles.length) {
          toast.error("请先上传文件");
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
      toast.error(err instanceof Error ? err.message : "添加失败");
    } finally {
      setAdding(false);
    }
  }, [handleOpenChange, kbId, onAdded, tab, uploadedFiles, url]);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>添加知识文档</DialogTitle>
          <DialogDescription>上传 PDF、Office、Markdown、文本文件，或添加网页链接</DialogDescription>
        </DialogHeader>
        <Tabs value={tab} onValueChange={(value) => setTab(value as "files" | "web")}>
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="files">文件</TabsTrigger>
            <TabsTrigger value="web">网页</TabsTrigger>
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
              选择文件 ({uploadedFiles.length} 已选)
            </Button>
          </TabsContent>
          <TabsContent value="web" className="space-y-3 pt-3">
            <div className="space-y-2">
              <Label htmlFor="kb-url">网页 URL</Label>
              <div className="flex items-center gap-2">
                <Globe2 className="text-muted-foreground size-4" />
                <Input id="kb-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." />
              </div>
            </div>
          </TabsContent>
        </Tabs>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleAdd} disabled={adding || uploading}>
            {adding && <Loader2 className="mr-2 size-4 animate-spin" />}
            添加并索引
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
