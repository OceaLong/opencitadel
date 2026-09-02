"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { api, errorMessage } from "@/lib/api-client";

type Run = {
  id: string;
  title: string;
  status: string;
  workflow: string;
  streamVersion: number;
  updatedAt: string;
};

export function RunsPage() {
  const router = useRouter();
  const [runs, setRuns] = useState<Run[]>([]);
  const [prompt, setPrompt] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRuns(await api<Run[]>("/runs"));
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, []);

  useEffect(() => void load(), [load]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ run_id?: string; runId?: string }>("/runs", {
        method: "POST",
        json: { prompt, title },
      });
      const id = result.run_id || result.runId;
      if (!id) throw new Error("服务端未返回 Run ID");
      router.push(`/runs/${id}`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Agent Runs</h1>
          <p className="muted">所有执行都经过命令、事件、Effect 与审批闭环。</p>
        </div>
        <button className="secondary" onClick={() => void load()}>刷新</button>
      </header>
      {error ? <p className="error">{error}</p> : null}
      <section className="card">
        <h2>启动 Run</h2>
        <form className="stack" onSubmit={create}>
          <label>
            标题
            <input value={title} maxLength={500} onChange={(event) => setTitle(event.target.value)} placeholder="可选" />
          </label>
          <label>
            任务
            <textarea value={prompt} required onChange={(event) => setPrompt(event.target.value)} placeholder="描述需要 Agent 完成的任务" />
          </label>
          <div className="actions"><button disabled={busy}>{busy ? "提交中…" : "启动"}</button></div>
        </form>
      </section>
      <section className="grid">
        {runs.map((run) => (
          <Link className="card" href={`/runs/${run.id}`} key={run.id}>
            <div className="row"><h2>{run.title || "Untitled Run"}</h2><span className="pill">{run.status}</span></div>
            <p className="muted">{run.workflow} · v{run.streamVersion}</p>
            <small className="muted">{new Date(run.updatedAt).toLocaleString()}</small>
          </Link>
        ))}
        {!runs.length ? <p className="muted">暂无 Run。</p> : null}
      </section>
    </div>
  );
}
