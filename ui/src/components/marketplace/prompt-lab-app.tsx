"use client";

import { useMemo, useState } from "react";
import { Copy, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
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

type PromptStyle = "agent" | "analysis" | "writing";

export function PromptLabApp({ initialPrompt = "" }: { initialPrompt?: string }) {
  const t = useTranslations("marketplaceApps.promptLab");
  const tShared = useTranslations("marketplaceApps.shared");
  const [idea, setIdea] = useState(initialPrompt);
  const [style, setStyle] = useState<PromptStyle>("agent");

  const generated = useMemo(() => {
    const goal = idea.trim() || t("generated.defaultGoal");
    return `${t(`styleHints.${style}`)}

${t("generated.goalLabel")}
${goal}

${t("generated.workMethodLabel")}
${t("generated.step1")}
${t("generated.step2")}
${t("generated.step3")}
${t("generated.step4")}
${t("generated.step5")}

${t("generated.outputFormatLabel")}
${t("generated.outputSummary")}
${t("generated.outputSteps")}
${t("generated.outputResult")}
${t("generated.outputRisks")}`;
  }, [idea, style, t]);

  const copy = async () => {
    await navigator.clipboard.writeText(generated);
    toast.message(t("copiedToast"));
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">{t("title")}</h2>
        <p className="text-muted-foreground mt-1 text-sm">{t("subtitle")}</p>
      </div>

      <Card>
        <CardContent className="space-y-4 py-5">
          <div className="space-y-2">
            <Label>{t("templateTypeLabel")}</Label>
            <Select value={style} onValueChange={(value) => setStyle(value as PromptStyle)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="agent">{t("styleAgent")}</SelectItem>
                <SelectItem value="analysis">{t("styleAnalysis")}</SelectItem>
                <SelectItem value="writing">{t("styleWriting")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="prompt-idea">{t("ideaLabel")}</Label>
            <Textarea
              id="prompt-idea"
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              placeholder={t("ideaPlaceholder")}
              className="min-h-28"
            />
          </div>
        </CardContent>
      </Card>

      <Card className="border-primary/20 bg-primary/5">
        <CardHeader className="flex flex-row items-center justify-between gap-3 pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="size-4" />
            {t("generatedTitle")}
          </CardTitle>
          <Button variant="outline" size="sm" onClick={copy}>
            <Copy className="size-4" />
            {tShared("copy")}
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
