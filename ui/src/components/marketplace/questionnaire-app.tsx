"use client";

import { useCallback, useEffect, useState } from "react";
import {
  BarChart3,
  Copy,
  Link2,
  Loader2,
  Plus,
  Share2,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

import { questionnaireApi } from "@/lib/api/questionnaire";
import type { QuestionItem, QuestionnaireData, QuestionnaireStatsData } from "@/lib/api/types";

const STORAGE_KEY = "my-manus-questionnaires";

type SavedEntry = { id: string; manage_token: string; title: string; slug: string };

function loadSaved(): SavedEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveEntry(entry: SavedEntry) {
  const list = loadSaved().filter((e) => e.id !== entry.id);
  list.unshift(entry);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, 20)));
}

function newQuestion(): QuestionItem {
  return {
    id: `q_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    type: "single",
    text: "",
    options: [
      { id: "a", text: "选项 A" },
      { id: "b", text: "选项 B" },
    ],
    required: true,
    rating_max: 5,
  };
}

export function QuestionnaireApp() {
  const [saved, setSaved] = useState<SavedEntry[]>([]);
  const [active, setActive] = useState<QuestionnaireData | null>(null);
  const [stats, setStats] = useState<QuestionnaireStatsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState("edit");

  useEffect(() => {
    setSaved(loadSaved());
  }, []);

  const createNew = async () => {
    setLoading(true);
    try {
      const data = await questionnaireApi.create({ title: "新问卷", questions: [newQuestion()] });
      saveEntry({
        id: data.id,
        manage_token: data.manage_token!,
        title: data.title,
        slug: data.slug,
      });
      setSaved(loadSaved());
      setActive(data);
      setTab("edit");
      toast.success("问卷已创建");
    } catch {
      toast.error("创建失败");
    } finally {
      setLoading(false);
    }
  };

  const openSaved = async (entry: SavedEntry) => {
    setLoading(true);
    try {
      const data = await questionnaireApi.get(entry.id, entry.manage_token);
      setActive(data);
      setTab(data.status === "published" ? "stats" : "edit");
      if (data.status === "published") {
        const s = await questionnaireApi.getStats(entry.id, entry.manage_token);
        setStats(s);
      }
    } catch {
      toast.error("加载失败");
    } finally {
      setLoading(false);
    }
  };

  const updateField = (field: "title" | "description", value: string) => {
    if (!active) return;
    setActive({ ...active, [field]: value });
  };

  const updateQuestion = (idx: number, patch: Partial<QuestionItem>) => {
    if (!active) return;
    const questions = [...active.questions];
    questions[idx] = { ...questions[idx], ...patch };
    setActive({ ...active, questions });
  };

  const addQuestion = () => {
    if (!active) return;
    setActive({ ...active, questions: [...active.questions, newQuestion()] });
  };

  const removeQuestion = (idx: number) => {
    if (!active) return;
    setActive({ ...active, questions: active.questions.filter((_, i) => i !== idx) });
  };

  const save = async () => {
    if (!active?.manage_token) return;
    setLoading(true);
    try {
      const data = await questionnaireApi.update(active.id, {
        manage_token: active.manage_token,
        title: active.title,
        description: active.description,
        questions: active.questions,
      });
      saveEntry({
        id: data.id,
        manage_token: data.manage_token!,
        title: data.title,
        slug: data.slug,
      });
      setSaved(loadSaved());
      setActive(data);
      toast.success("已保存");
    } catch {
      toast.error("保存失败");
    } finally {
      setLoading(false);
    }
  };

  const publish = async () => {
    if (!active?.manage_token) return;
    setLoading(true);
    try {
      await save();
      const data = await questionnaireApi.publish(active.id, {
        manage_token: active.manage_token,
      });
      saveEntry({
        id: data.id,
        manage_token: data.manage_token!,
        title: data.title,
        slug: data.slug,
      });
      setSaved(loadSaved());
      setActive(data);
      toast.success("已发布");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "发布失败");
    } finally {
      setLoading(false);
    }
  };

  const loadStats = useCallback(async () => {
    if (!active?.manage_token) return;
    try {
      const s = await questionnaireApi.getStats(active.id, active.manage_token);
      setStats(s);
    } catch {
      toast.error("加载统计失败");
    }
  }, [active]);

  const shareUrl =
    active?.slug && typeof window !== "undefined"
      ? `${window.location.origin}/q/${active.slug}`
      : "";

  const copyShare = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      toast.success("分享链接已复制");
    } catch {
      toast.error("复制失败");
    }
  };

  if (!active) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-foreground text-lg font-semibold">自定义问卷</h2>
            <p className="text-muted-foreground mt-1 text-sm">创建问卷、发布分享、查看统计</p>
          </div>
          <Button onClick={createNew} disabled={loading}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            新建问卷
          </Button>
        </div>
        {saved.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-foreground text-sm font-medium">我的问卷</h3>
            <div className="grid gap-2 sm:grid-cols-2">
              {saved.map((e) => (
                <Card
                  key={e.id}
                  className="hover:border-primary/40 cursor-pointer transition-colors"
                  onClick={() => openSaved(e)}
                >
                  <CardContent className="flex items-center justify-between pt-4">
                    <div>
                      <p className="text-foreground text-sm font-medium">{e.title}</p>
                      <p className="text-muted-foreground text-xs">/{e.slug}</p>
                    </div>
                    <BarChart3 className="text-muted-foreground size-4" />
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button variant="ghost" size="sm" onClick={() => setActive(null)}>
          返回列表
        </Button>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={save} disabled={loading}>
            保存
          </Button>
          {active.status === "draft" && (
            <Button size="sm" onClick={publish} disabled={loading}>
              发布
            </Button>
          )}
          {active.status === "published" && (
            <Button variant="outline" size="sm" onClick={copyShare}>
              <Copy className="mr-1 size-3.5" />
              复制链接
            </Button>
          )}
        </div>
      </div>

      <Tabs value={tab} onValueChange={(v) => { setTab(v); if (v === "stats") loadStats(); }}>
        <TabsList>
          <TabsTrigger value="edit">编辑</TabsTrigger>
          <TabsTrigger value="share">分享</TabsTrigger>
          <TabsTrigger value="stats">统计</TabsTrigger>
        </TabsList>

        <TabsContent value="edit" className="space-y-4">
          <div className="space-y-2">
            <Label>标题</Label>
            <Input value={active.title} onChange={(e) => updateField("title", e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label>描述</Label>
            <Textarea
              value={active.description}
              onChange={(e) => updateField("description", e.target.value)}
              rows={2}
            />
          </div>
          <div className="space-y-3">
            {active.questions.map((q, idx) => (
              <Card key={q.id}>
                <CardContent className="space-y-3 pt-4">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-muted-foreground text-xs">第 {idx + 1} 题</span>
                    <Button variant="ghost" size="icon" onClick={() => removeQuestion(idx)}>
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                  <Input
                    value={q.text}
                    onChange={(e) => updateQuestion(idx, { text: e.target.value })}
                    placeholder="题目内容"
                  />
                  <Select
                    value={q.type}
                    onValueChange={(v) =>
                      updateQuestion(idx, { type: v as QuestionItem["type"] })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="single">单选</SelectItem>
                      <SelectItem value="multiple">多选</SelectItem>
                      <SelectItem value="rating">评分</SelectItem>
                      <SelectItem value="text">文本</SelectItem>
                    </SelectContent>
                  </Select>
                  {(q.type === "single" || q.type === "multiple") &&
                    q.options?.map((opt, oi) => (
                      <Input
                        key={opt.id}
                        value={opt.text}
                        onChange={(e) => {
                          const options = [...(q.options ?? [])];
                          options[oi] = { ...opt, text: e.target.value };
                          updateQuestion(idx, { options });
                        }}
                        placeholder={`选项 ${opt.id.toUpperCase()}`}
                      />
                    ))}
                </CardContent>
              </Card>
            ))}
            <Button variant="outline" onClick={addQuestion}>
              <Plus className="mr-1 size-4" />
              添加题目
            </Button>
          </div>
        </TabsContent>

        <TabsContent value="share" className="space-y-4">
          {active.status === "published" ? (
            <Card>
              <CardContent className="space-y-3 pt-6">
                <div className="flex items-center gap-2">
                  <Link2 className="text-primary size-4" />
                  <span className="text-foreground text-sm font-medium">填写链接</span>
                </div>
                <p className="text-muted-foreground break-all text-sm">{shareUrl}</p>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={copyShare}>
                    <Copy className="mr-1 size-4" />
                    复制
                  </Button>
                  <Button variant="outline" onClick={() => window.open(shareUrl, "_blank")}>
                    <Share2 className="mr-1 size-4" />
                    预览
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : (
            <p className="text-muted-foreground text-sm">请先发布问卷后获取分享链接</p>
          )}
        </TabsContent>

        <TabsContent value="stats" className="space-y-4">
          {stats ? (
            <>
              <p className="text-muted-foreground text-sm">
                共 {stats.total_responses} 份回复
              </p>
              {Object.entries(stats.per_question).map(([qid, q]) => (
                <Card key={qid}>
                  <CardContent className="space-y-2 pt-4">
                    <p className="text-foreground text-sm font-medium">{q.text}</p>
                    {q.counts && q.labels && (
                      <div className="space-y-1">
                        {Object.entries(q.counts).map(([oid, cnt]) => {
                          const total = stats.total_responses || 1;
                          const pct = Math.round((cnt / total) * 100);
                          return (
                            <div key={oid} className="space-y-1">
                              <div className="flex justify-between text-xs">
                                <span>{q.labels?.[oid] ?? oid}</span>
                                <span>{cnt} ({pct}%)</span>
                              </div>
                              <div className="bg-muted h-2 rounded-full">
                                <div
                                  className="bg-primary h-2 rounded-full"
                                  style={{ width: `${pct}%` }}
                                />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {q.type === "rating" && (
                      <p className="text-muted-foreground text-sm">
                        平均分 {q.average}（{q.count} 人评分）
                      </p>
                    )}
                    {q.responses && q.responses.length > 0 && (
                      <ul className="text-muted-foreground space-y-1 text-xs">
                        {q.responses.slice(0, 10).map((r, i) => (
                          <li key={i}>
                            {r.name ? `${r.name}: ` : ""}
                            {r.text}
                          </li>
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>
              ))}
            </>
          ) : (
            <Button variant="outline" onClick={loadStats}>
              加载统计
            </Button>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
