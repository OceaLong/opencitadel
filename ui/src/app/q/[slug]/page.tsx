"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CheckCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { questionnaireApi } from "@/lib/api/questionnaire";
import type { QuestionItem, QuestionnaireData } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export default function PublicQuestionnairePage() {
  const params = useParams();
  const slug = params.slug as string;

  const [questionnaire, setQuestionnaire] = useState<QuestionnaireData | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [name, setName] = useState("");
  const [answers, setAnswers] = useState<Record<string, unknown>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await questionnaireApi.getPublic(slug);
      setQuestionnaire(data);
    } catch {
      toast.error("问卷不存在或未发布");
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    load();
  }, [load]);

  const setAnswer = (qid: string, value: unknown) => {
    setAnswers((prev) => ({ ...prev, [qid]: value }));
  };

  const toggleMultiple = (qid: string, optId: string) => {
    const current = (answers[qid] as string[]) ?? [];
    const next = current.includes(optId)
      ? current.filter((v) => v !== optId)
      : [...current, optId];
    setAnswer(qid, next);
  };

  const submit = async () => {
    if (!questionnaire) return;
    for (const q of questionnaire.questions) {
      const value = answers[q.id];
      const empty =
        value === undefined ||
        value === null ||
        value === "" ||
        (q.type === "multiple" && Array.isArray(value) && value.length === 0);
      if (q.required !== false && empty) {
        toast.error(`请回答：${q.text}`);
        return;
      }
    }
    setSubmitting(true);
    try {
      await questionnaireApi.submitResponse(slug, {
        answers,
        respondent_name: name || undefined,
      });
      setSubmitted(true);
    } catch {
      toast.error("提交失败");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );
  }

  if (!questionnaire) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-muted-foreground text-sm">问卷不可用</p>
      </div>
    );
  }

  if (submitted) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-6">
        <CheckCircle className="text-primary size-12" />
        <h1 className="text-foreground text-xl font-semibold">感谢参与！</h1>
        <p className="text-muted-foreground text-sm">你的回答已成功提交</p>
      </div>
    );
  }

  return (
    <div className="from-background via-background to-muted/30 min-h-screen bg-gradient-to-br p-4 sm:p-8">
      <div className="mx-auto max-w-lg space-y-6">
        <div className="text-center">
          <h1 className="text-foreground text-2xl font-semibold">{questionnaire.title}</h1>
          {questionnaire.description && (
            <p className="text-muted-foreground mt-2 text-sm">{questionnaire.description}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label>你的名字（可选）</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="匿名" />
        </div>

        {questionnaire.questions.map((q: QuestionItem, idx) => (
          <Card key={q.id}>
            <CardContent className="space-y-3 pt-5">
              <p className="text-foreground text-sm font-medium">
                {idx + 1}. {q.text}
                {q.required !== false && <span className="text-destructive ml-1">*</span>}
              </p>
              {(q.type === "single" || q.type === "multiple") &&
                q.options?.map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() =>
                      q.type === "single"
                        ? setAnswer(q.id, opt.id)
                        : toggleMultiple(q.id, opt.id)
                    }
                    className={cn(
                      "flex w-full rounded-lg border px-4 py-2.5 text-left text-sm transition-colors",
                      q.type === "single" && answers[q.id] === opt.id
                        ? "border-primary bg-primary/5"
                        : q.type === "multiple" &&
                            (answers[q.id] as string[] | undefined)?.includes(opt.id)
                          ? "border-primary bg-primary/5"
                          : "border-border/70 hover:border-primary/40",
                    )}
                  >
                    {opt.text}
                  </button>
                ))}
              {q.type === "rating" && (
                <div className="flex gap-2">
                  {Array.from({ length: q.rating_max ?? 5 }, (_, i) => i + 1).map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => setAnswer(q.id, n)}
                      className={cn(
                        "flex size-10 items-center justify-center rounded-full border text-sm",
                        answers[q.id] === n
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-border/70",
                      )}
                    >
                      {n}
                    </button>
                  ))}
                </div>
              )}
              {q.type === "text" && (
                <Textarea
                  value={(answers[q.id] as string) ?? ""}
                  onChange={(e) => setAnswer(q.id, e.target.value)}
                  rows={3}
                />
              )}
            </CardContent>
          </Card>
        ))}

        <Button className="w-full" onClick={submit} disabled={submitting}>
          {submitting ? <Loader2 className="size-4 animate-spin" /> : "提交"}
        </Button>
      </div>
    </div>
  );
}
