"use client";

import { useCallback, useRef, useState } from "react";
import { FolderArchive, GitBranch, Loader2, Upload } from "lucide-react";
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
    setUploading(true);
    try {
      const info = await fileApi.uploadFile({ file });
      setUploadedZipId(info.id);
      if (!name) setName(file.name.replace(/\.(zip|tar\.gz|tgz)$/i, ""));
      toast.success("压缩包上传成功");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "上传失败");
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
      toast.success(`已上传 ${ids.length} 个文件`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "上传失败");
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
          toast.error("请输入 Git 仓库 URL");
          return;
        }
        codebase = await codebaseApi.create({
          name: name || "Git 代码库",
          source_type: "git",
          git_url: gitUrl.trim(),
        });
      } else if (tab === "zip") {
        if (!uploadedZipId) {
          toast.error("请先上传 zip 压缩包");
          return;
        }
        codebase = await codebaseApi.create({
          name: name || "Zip 代码库",
          source_type: "zip",
          file_id: uploadedZipId,
        });
      } else {
        if (!uploadedFileIds.length) {
          toast.error("请先上传文件");
          return;
        }
        codebase = await codebaseApi.create({
          name: name || "文件代码库",
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
      toast.error(err instanceof Error ? err.message : "创建失败");
    } finally {
      setCreating(false);
    }
  }, [tab, name, gitUrl, uploadedZipId, uploadedFileIds, onCreated, onOpenChange]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>新建代码知识库</DialogTitle>
          <DialogDescription>
            上传 zip、Git 仓库或多文件，系统将自动分析并建立专属代码知识库
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="cb-name">名称</Label>
            <Input
              id="cb-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="我的项目"
            />
          </div>
          <Tabs value={tab} onValueChange={(v) => setTab(v as CodebaseSourceType)}>
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="zip">Zip</TabsTrigger>
              <TabsTrigger value="git">Git</TabsTrigger>
              <TabsTrigger value="files">文件</TabsTrigger>
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
                {uploadedZipId ? "已上传，点击更换" : "选择 zip 压缩包"}
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
                选择文件 ({uploadedFileIds.length} 已选)
              </Button>
            </TabsContent>
          </Tabs>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleCreate} disabled={creating || uploading}>
            {creating && <Loader2 className="mr-2 size-4 animate-spin" />}
            创建并分析
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
