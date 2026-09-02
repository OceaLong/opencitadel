"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { api, API_BASE, errorMessage } from "@/lib/api-client";

type FileItem = { id: string; filename: string; size: number; digest: string };
type Knowledge = { id: string; name: string; activeVersionId?: string | null; archivedAt?: string | null };
type Disposition = { planHash: string; confirmation: string };

export default function KnowledgePage() {
  const [files, setFiles] = useState<FileItem[]>([]);
  const [knowledge, setKnowledge] = useState<Knowledge[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [nextFiles, nextKnowledge] = await Promise.all([
        api<FileItem[]>("/files"),
        api<Knowledge[]>("/knowledge-bases"),
      ]);
      setFiles(nextFiles);
      setKnowledge(nextKnowledge);
      setError("");
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, []);
  useEffect(() => void load(), [load]);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = event.currentTarget.elements.namedItem("file") as HTMLInputElement;
    if (!input.files?.[0]) return;
    const form = new FormData();
    form.set("file", input.files[0]);
    try {
      await api("/files", { method: "POST", body: form });
      event.currentTarget.reset();
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function createKnowledge(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/knowledge-bases", { method: "POST", json: { name } });
      setName("");
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function startBuild(id: string) {
    if (!selected.length) return setError("请先选择至少一个文件");
    try {
      await api(`/knowledge-bases/${id}/builds`, { method: "POST", json: { fileIds: selected } });
      setSelected([]);
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function archive(item: Knowledge) {
    try {
      const plan = await api<Disposition>(`/knowledge-bases/${item.id}/disposition?action=archive`);
      if (!window.confirm(plan.confirmation)) return;
      await api(`/knowledge-bases/${item.id}/commands/archive`, {
        method: "POST",
        json: { planHash: plan.planHash, confirmation: plan.confirmation },
      });
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return <div className="stack">
    <header className="page-header"><div><h1>知识与文件</h1><p className="muted">文件按摘要存储；知识构建先生成候选版本，成功后才原子发布。</p></div><button className="secondary" onClick={() => void load()}>刷新</button></header>
    {error ? <p className="error">{error}</p> : null}
    <div className="grid">
      <section className="card stack">
        <h2>文件</h2>
        <form className="row" onSubmit={upload}><input name="file" required type="file" /><button>上传</button></form>
        {files.map((file) => <label className="row" key={file.id}><span><input checked={selected.includes(file.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, file.id] : current.filter((id) => id !== file.id))} style={{ width: "auto" }} type="checkbox" /> {file.filename}</span><a className="muted" href={`${API_BASE}/files/${file.id}/download`}>{file.size} B</a></label>)}
      </section>
      <section className="card stack">
        <h2>知识库</h2>
        <form className="row" onSubmit={createKnowledge}><input required value={name} onChange={(event) => setName(event.target.value)} placeholder="知识库名称" /><button>创建</button></form>
        {knowledge.map((item) => <article className="card" key={item.id}><div className="row"><div><strong>{item.name}</strong><p className="muted">活动版本：{item.activeVersionId || "尚未发布"}</p></div><div className="actions"><button onClick={() => void startBuild(item.id)}>用所选文件构建</button><button className="danger" onClick={() => void archive(item)}>归档</button></div></div></article>)}
      </section>
    </div>
  </div>;
}
