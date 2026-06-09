"use client";

import { useMemo, useState } from "react";
import { Copy, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

const STYLE_HINTS = {
  agent: "你是一个可靠的 AI Agent。先澄清目标，再分解任务，必要时调用工具，最后给出可验证结果。",
  analysis: "你是资深分析师。请列出假设、数据口径、关键发现、风险和下一步建议。",
  writing: "你是专业内容编辑。请先确认受众与语气，再输出结构清晰、可直接发布的内容。",
};

type PromptStyle = keyof typeof STYLE_HINTS;

export function PromptLabApp({ initialPrompt = "" }: { initialPrompt?: string }) {
  const [idea, setIdea] = useState(initialPrompt);
  const [style, setStyle] = useState<PromptStyle>("agent");

  const generated = useMemo(() => {
    const goal = idea.trim() || "在这里描述你的目标";
    return `${STYLE_HINTS[style]}

目标：
${goal}

工作方式：
1. 先用 1-2 句话复述目标与约束。
2. 如果信息不足，最多提出 2 个关键澄清问题。
3. 将任务拆成可执行步骤，并在每一步说明判断依据。
4. 输出结果时区分结论、证据、风险和后续动作。
5. 对不确定内容明确标注，不编造来源或数据。

输出格式：
- 摘要
- 关键步骤
- 最终结果
- 风险与待确认项`;
  }, [idea, style]);

  const copy = async () => {
    await navigator.clipboard.writeText(generated);
    toast.message("提示词已复制");
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">提示词工坊</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          将粗略想法整理为结构化、可复用、可控的高质量提示词
        </p>
      </div>

      <Card>
        <CardContent className="space-y-4 py-5">
          <div className="space-y-2">
            <Label>模板类型</Label>
            <Select value={style} onValueChange={(value) => setStyle(value as PromptStyle)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="agent">Agent 执行</SelectItem>
                <SelectItem value="analysis">分析报告</SelectItem>
                <SelectItem value="writing">内容创作</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="prompt-idea">你的粗略想法</Label>
            <Textarea
              id="prompt-idea"
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              placeholder="例如：帮我做竞品调研，并输出表格和行动建议"
              className="min-h-28"
            />
          </div>
        </CardContent>
      </Card>

      <Card className="border-primary/20 bg-primary/5">
        <CardHeader className="flex flex-row items-center justify-between gap-3 pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="size-4" />
            生成提示词
          </CardTitle>
          <Button variant="outline" size="sm" onClick={copy}>
            <Copy className="size-4" />
            复制
          </Button>
        </CardHeader>
        <CardContent>
          <pre className="border-border/70 bg-background/80 text-foreground overflow-auto rounded-xl border p-4 text-sm leading-relaxed whitespace-pre-wrap">
            {generated}
          </pre>
        </CardContent>
      </Card>
    </div>
  );
}
