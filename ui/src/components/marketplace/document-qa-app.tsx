"use client";

import { useRef, useState } from "react";
import { FileQuestion, Loader2, Send, Upload } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import { fileApi } from "@/lib/api/file";
import { marketplaceApi } from "@/lib/api/marketplace";
import { useRequireAuth } from "@/hooks/use-require-auth";

const MAX_SIZE = 8 * 1024 * 1024;

export function DocumentQaApp({ initialQuestion = "" }: { initialQuestion?: string }) {
  const { requireAuth } = useRequireAuth();
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileId, setFileId] = useState("");
  const [fileName, setFileName] = useState("");
  const [question, setQuestion] = useState(initialQuestion);
  const [answer, setAnswer] = useState("");
  const [sourceSummary, setSourceSummary] = useState("");
  const [loading, setLoading] = useState(false);

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    if (!requireAuth("登录后即可使用 AI 文档问答")) return;
    if (file.size > MAX_SIZE) {
      toast.error("文件不能超过 8MB");
      return;
    }
    setLoading(true);
    setAnswer("");
    try {
      const uploaded = await fileApi.uploadFile({ file });
      setFileId(uploaded.id);
      setFileName(file.name);
      toast.message("资料已上传，可以开始提问");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "上传失败");
    } finally {
      setLoading(false);
    }
  };

  const ask = async () => {
    if (!fileId || !question.trim()) {
      toast.error("请先上传资料并输入问题");
      return;
    }
    if (!requireAuth("登录后即可使用 AI 文档问答")) return;
    setLoading(true);
    setAnswer("");
    try {
      const data = await marketplaceApi.askDocumentQuestion({
        file_id: fileId,
        question: question.trim(),
      });
      setAnswer(data.answer);
      setSourceSummary(data.source_summary);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "问答失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">文档/图片问答</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          上传文本资料或截图，让 AI 提炼重点并回答你的具体问题
        </p>
      </div>

      <Card>
        <CardContent className="space-y-4 py-5">
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            accept="image/*,.txt,.md,.csv,.json,.log"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="border-muted-foreground/25 hover:border-primary/40 hover:bg-muted/30 flex min-h-32 w-full flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed p-6 text-center transition-colors"
          >
            <div className="bg-muted flex size-12 items-center justify-center rounded-full">
              <Upload className="text-muted-foreground size-6" />
            </div>
            <div>
              <p className="text-foreground text-sm font-medium">
                {fileName || "上传资料或截图"}
              </p>
              <p className="text-muted-foreground mt-1 text-xs">
                支持图片、txt、md、csv、json、log，最大 8MB
              </p>
            </div>
          </button>

          <div className="space-y-2">
            <Label htmlFor="doc-question">你想问什么？</Label>
            <Textarea
              id="doc-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="例如：总结重点、找出风险、解释截图里的报错"
              className="min-h-24"
            />
          </div>
          <Button onClick={ask} disabled={loading || !fileId}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            提问
          </Button>
        </CardContent>
      </Card>

      {answer ? (
        <Card className="border-primary/20 bg-primary/5">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <FileQuestion className="size-4" />
              AI 回答
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {sourceSummary && <p className="text-muted-foreground text-xs">{sourceSummary}</p>}
            <div className="text-foreground text-sm leading-relaxed whitespace-pre-wrap">{answer}</div>
          </CardContent>
        </Card>
      ) : (
        <div className="bg-muted/20 flex flex-col items-center justify-center rounded-xl border border-dashed px-4 py-10 text-center">
          <FileQuestion className="text-muted-foreground/50 mb-3 size-10" />
          <p className="text-foreground text-sm font-medium">上传资料后开始问答</p>
          <p className="text-muted-foreground mt-1 max-w-sm text-xs">
            适合会议纪要、日志、截图报错、结构化文本的快速理解
          </p>
        </div>
      )}
    </div>
  );
}
