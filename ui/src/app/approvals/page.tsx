"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { api, errorMessage } from "@/lib/api-client";

type Approval = {
  id: string;
  runId: string;
  subject: string;
  riskSummary: Record<string, unknown>;
  status: string;
  requestedAt: string;
  expiresAt?: string | null;
};

export default function ApprovalsPage() {
  const [items, setItems] = useState<Approval[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setItems(await api<Approval[]>("/approvals?status=pending"));
      setError("");
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, []);
  useEffect(() => void load(), [load]);

  async function decide(item: Approval, decision: "approved" | "rejected") {
    const feedback = window.prompt("审批意见（可选）", "") ?? "";
    try {
      await api(`/approvals/${item.id}/commands/decide`, {
        method: "POST",
        json: { decision, feedback },
      });
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return <div className="stack">
    <header className="page-header"><div><h1>审批中心</h1><p className="muted">评审人集合在请求时冻结，决定只释放对应 Effect。</p></div><button className="secondary" onClick={() => void load()}>刷新</button></header>
    {error ? <p className="error">{error}</p> : null}
    {items.map((item) => <article className="card stack" key={item.id}>
      <div className="row"><div><h2>{item.subject}</h2><Link className="muted" href={`/runs/${item.runId}`}>Run {item.runId}</Link></div><span className="pill">{item.status}</span></div>
      <pre>{JSON.stringify(item.riskSummary, null, 2)}</pre>
      <div className="row"><small className="muted">请求：{new Date(item.requestedAt).toLocaleString()}</small><div className="actions"><button className="danger" onClick={() => void decide(item, "rejected")}>拒绝</button><button onClick={() => void decide(item, "approved")}>批准</button></div></div>
    </article>)}
    {!items.length ? <section className="card muted">当前没有待处理审批。</section> : null}
  </div>;
}
