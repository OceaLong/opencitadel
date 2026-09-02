"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { api, errorMessage } from "@/lib/api-client";

type Run = { id: string; title: string; status: string; workflow: string; streamVersion: number };
type Event = { version: number; type: string; payload: Record<string, unknown>; occurredAt: string };
type Disposition = { planHash: string; confirmation: string };

export default function RunPage() {
  const id = String(useParams<{ id: string }>().id);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [nextRun, history] = await Promise.all([
        api<Run>(`/runs/${id}`),
        api<Event[]>(`/runs/${id}/history`),
      ]);
      setRun(nextRun);
      setEvents(history);
      setError("");
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, [id]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [load]);

  async function command(path: string, body: object) {
    try {
      await api(`/runs/${id}/commands/${path}`, { method: "POST", json: body });
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function submitPrompt(event: FormEvent) {
    event.preventDefault();
    await command("prompt", { prompt, expectedStreamVersion: run?.streamVersion });
    setPrompt("");
  }

  async function archive() {
    try {
      const plan = await api<Disposition>(`/runs/${id}/disposition?action=archive`);
      if (!window.confirm(plan.confirmation)) return;
      await command("archive", {
        planHash: plan.planHash,
        confirmation: plan.confirmation,
        expectedStreamVersion: run?.streamVersion,
      });
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <div className="stack">
      <header className="page-header">
        <div><Link className="muted" href="/">← Runs</Link><h1>{run?.title || "Run"}</h1><p className="muted">{id}</p></div>
        <div className="actions">
          <span className="pill">{run?.status || "loading"}</span>
          <button className="secondary" onClick={() => void command("cancel", { reason: "user_requested", expectedStreamVersion: run?.streamVersion })}>取消</button>
          <button className="danger" onClick={() => void archive()}>归档</button>
        </div>
      </header>
      {error ? <p className="error">{error}</p> : null}
      <section className="card">
        <form className="row" onSubmit={submitPrompt}>
          <input value={prompt} required onChange={(event) => setPrompt(event.target.value)} placeholder="继续向此 Run 提交任务" />
          <button>发送</button>
        </form>
      </section>
      <section className="card">
        <h2>不可变事件时间线</h2>
        <div className="timeline">
          {events.map((event) => (
            <article key={event.version}>
              <div className="row"><strong>v{event.version} · {event.type}</strong><small className="muted">{new Date(event.occurredAt).toLocaleString()}</small></div>
              <pre>{JSON.stringify(event.payload, null, 2)}</pre>
            </article>
          ))}
          {!events.length ? <p className="muted">等待事件。</p> : null}
        </div>
      </section>
    </div>
  );
}
