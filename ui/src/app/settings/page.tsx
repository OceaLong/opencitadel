"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "@/lib/api-client";

type Endpoint = { id: string; displayName: string; provider: string; baseUrl: string; visibility: string; hasCredential: boolean };
type Model = { id: string; endpointId: string; displayName: string; modelName: string; visibility: string };
type Binding = { id: string; purpose: string; modelId: string; scopeType: string };
type MCP = { id: string; name: string; transport: string; visibility: string; hasSecretConfig: boolean };

export default function SettingsPage() {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [servers, setServers] = useState<MCP[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const values = await Promise.all([
        api<Endpoint[]>("/inference/endpoints"),
        api<Model[]>("/inference/models"),
        api<Binding[]>("/inference/bindings"),
        api<MCP[]>("/integrations/mcp"),
      ]);
      setEndpoints(values[0]); setModels(values[1]); setBindings(values[2]); setServers(values[3]); setError("");
    } catch (caught) { setError(errorMessage(caught)); }
  }, []);
  useEffect(() => void load(), [load]);

  async function createEndpoint(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api("/inference/endpoints", { method: "POST", json: {
        displayName: data.get("displayName"), provider: "openai",
        baseUrl: data.get("baseUrl"), credential: data.get("credential"), visibility: "private",
      } });
      event.currentTarget.reset(); await load();
    } catch (caught) { setError(errorMessage(caught)); }
  }

  async function createModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api("/inference/models", { method: "POST", json: {
        endpointId: data.get("endpointId"), displayName: data.get("displayName"),
        modelName: data.get("modelName"), kind: "chat", settings: {}, capabilities: {}, visibility: "private",
      } });
      event.currentTarget.reset(); await load();
    } catch (caught) { setError(errorMessage(caught)); }
  }

  async function bind(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api("/inference/bindings/agent", { method: "PUT", json: { modelId: data.get("modelId"), scopeType: "current" } });
      await load();
    } catch (caught) { setError(errorMessage(caught)); }
  }

  async function createMcp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const config = JSON.parse(String(data.get("config") || "{}"));
      const capabilityCatalog = JSON.parse(String(data.get("catalog") || "{}"));
      await api("/integrations/mcp", { method: "POST", json: {
        name: data.get("name"), transport: data.get("transport"), config, capabilityCatalog, visibility: "private",
      } });
      event.currentTarget.reset(); await load();
    } catch (caught) { setError(errorMessage(caught)); }
  }

  async function remove(path: string) {
    if (!window.confirm("确认删除？已有 Run 使用的是冻结能力清单，不会被静默改写。")) return;
    try { await api(path, { method: "DELETE" }); await load(); } catch (caught) { setError(errorMessage(caught)); }
  }

  return <div className="stack">
    <header className="page-header"><div><h1>推理与 MCP</h1><p className="muted">密钥加密持久化；Run 启动时冻结模型与工具能力。</p></div><button className="secondary" onClick={() => void load()}>刷新</button></header>
    {error ? <p className="error">{error}</p> : null}
    <div className="grid">
      <section className="card stack"><h2>1. OpenAI 兼容端点</h2><form className="stack" onSubmit={createEndpoint}><label>名称<input name="displayName" required /></label><label>Base URL<input name="baseUrl" defaultValue="https://api.openai.com/v1" required /></label><label>API Key<input name="credential" type="password" required /></label><button>添加端点</button></form>{endpoints.map((item) => <div className="row" key={item.id}><span>{item.displayName}<small className="muted"> · {item.baseUrl}</small></span><button className="danger" onClick={() => void remove(`/inference/endpoints/${item.id}`)}>删除</button></div>)}</section>
      <section className="card stack"><h2>2. 模型与绑定</h2><form className="stack" onSubmit={createModel}><label>端点<select name="endpointId" required>{endpoints.map((item) => <option value={item.id} key={item.id}>{item.displayName}</option>)}</select></label><label>显示名<input name="displayName" required /></label><label>模型名<input name="modelName" placeholder="gpt-5" required /></label><button>添加模型</button></form><form className="row" onSubmit={bind}><select name="modelId" required>{models.map((item) => <option value={item.id} key={item.id}>{item.displayName}</option>)}</select><button>设为 Agent 模型</button></form>{bindings.map((item) => <p className="muted" key={item.id}>{item.purpose}: {item.modelId} ({item.scopeType})</p>)}</section>
      <section className="card stack"><h2>3. MCP Server</h2><form className="stack" onSubmit={createMcp}><label>名称<input name="name" required /></label><label>传输<select name="transport"><option value="streamable_http">Streamable HTTP</option><option value="stdio">stdio</option></select></label><label>连接配置 JSON<textarea name="config" defaultValue={'{"url":"https://example.com/mcp"}'} /></label><label>能力目录 JSON<textarea name="catalog" defaultValue={'{"tools":[]}'} /></label><button>添加 MCP</button></form>{servers.map((item) => <div className="row" key={item.id}><span>{item.name} <small className="muted">{item.transport}</small></span><button className="danger" onClick={() => void remove(`/integrations/mcp/${item.id}`)}>删除</button></div>)}</section>
    </div>
  </div>;
}
