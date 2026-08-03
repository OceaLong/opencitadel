[English](ops-patrol.md)

# Ops Patrol 运维手册

本文覆盖生产启用、Collector 部署、最小权限、验证、证据、保留、恢复与排障。领域契约见 [Ops Patrol 架构](../architecture/ops-patrol.zh-CN.md)。

## 生产就绪清单

- API、Worker、PostgreSQL、Redis、Migration 及支持工具调用的模型均健康。
- `AUDIT_SIGNING_KEY` 独立生成并由 Secret Manager 保存，Key ID/历史 Key Map 遵循既有审计轮换流程。
- Collector 使用专用 Kubernetes 只读 ServiceAccount，且不暴露到公网。
- 每个 Namespace、Workload、Prometheus Query、HTTP/TLS/备份 Endpoint 与依赖都已显式评审和注册。
- Collector Egress NetworkPolicy 与目标一致；注册 Endpoint 为内网最小状态接口，URL 不嵌入凭据。
- 九个 MCP Tool Policy 全部为 `integration_read` + `read_only` + `safe` + `never` approval。
- Fixture Replay 在生产保持关闭。
- 启用计划前已通过 dry-run 和一次手动 Run。

## 安全边界

Collector 只暴露九个固定工具：能力发现、Kubernetes 工作负载/事件/日志，以及已注册的 Prometheus、HTTP、TLS、备份和依赖探针。它不提供 Shell、浏览器、写 API、任意 URL 或任意 PromQL。采集字符串一律视为不可信数据，最终状态和证据完整率由 API 重新计算。

Kubernetes RBAC 仅允许 `get`、`list`、`watch`，排除 Secret、exec、attach、impersonation 和全部变更动词。容器使用 UID/GID 10001、只读根文件系统、删除全部 Linux Capability、`RuntimeDefault` seccomp，仅挂载 32 MiB 可写 `/tmp`。

## 部署

### Docker Compose

```bash
docker compose --profile patrol up -d --build opencitadel-ops-collector
docker compose ps opencitadel-ops-collector
```

该 Profile 可验证 MCP 传输/配置并运行已注册的非 Kubernetes 探针，但不会挂载宿主机 kubeconfig。真实 Kubernetes 检查应使用 Helm/Kustomize 专用 ServiceAccount；不要通过挂载高权限宿主凭据解决本地访问。

### Helm

构建或拉取 `opencitadel-ops-collector` 镜像后，使用受保护的 Values 文件：

```bash
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --values production-values.yaml \
  --set opsCollector.enabled=true \
  --set opsCollector.image.repository=your-registry/opencitadel-ops-collector \
  --set opsCollector.image.tag=YOUR_RELEASE_TAG \
  --set opsCollector.targetRef=cluster-a \
  --set-json 'opsCollector.allowedNamespaces=["opencitadel"]'
```

在 Values 文件配置 `allowedWorkloads` 与所有 `registered*` Map；完整 Schema 与示例见 [Collector README](../../ops-collector/README.zh-CN.md#配置参考)。UI 向导会启用全部内置检查，因此需要以下 ID（仅完整自定义 API Config 可禁用部分检查）：

- `pvc-utilization`、`app-5xx-ratio`
- `primary-endpoint`、`primary-tls`
- `primary-database`、`primary-dependencies`

若 `opsCollector.serviceAccount.create=false`，必须设置 `opsCollector.serviceAccount.name` 并预先授予等价最小只读权限。不得授予 Secret 读取或任何变更动词。

### Kustomize

```bash
kubectl kustomize deploy/kustomize/ops-collector >/dev/null
kubectl apply -k deploy/kustomize/ops-collector
```

仓库内 Kustomization 应作为 Base。用于非一次性环境前，必须 Patch 镜像/Tag、Target Ref、白名单、注册目标 JSON 环境变量、资源限制、Namespace 和 NetworkPolicy Egress。

### 网络位置

Collector Service 保持内部 `ClusterIP`，Ingress 仅允许 API/Worker 访问 TCP 8090。Egress 仅允许 DNS、Kubernetes API 和注册目标的准确端口。仅按端口限制的 NetworkPolicy 无法验证主机名或 URL Path，因此注册表仍是 SSRF 权威边界。

## 注册 MCP Server

Helm Service URL 通常为：

```text
http://opencitadel-ops-collector:8090/mcp
```

在 **设置 → 集成** 中创建 Streamable HTTP 连接并启用。Developer Preview 表单暂不编辑 Tool Policy；管理员需通过已认证的 `POST /api/app-config/mcp-servers/ops-collector/update`（或等价的受控引导流程）持久化以下完整请求体：

```json
{
  "mcpServers": {
    "ops-collector": {
      "transport": "streamable_http",
      "enabled": true,
      "url": "http://opencitadel-ops-collector:8090/mcp",
      "tool_policies": {
        "get_capabilities": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "k8s_workload_summary": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "k8s_recent_events": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "k8s_pod_logs": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "prom_query": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "http_probe": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "certificate_status": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "backup_status": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "dependency_status": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"}
      }
    }
  }
}
```

常规 UI 连接编辑不会提交 `tool_policies`，Service 会保留已持久化 Map。Collector Tool Catalog 变化时必须重新应用并评审该 Map。

任一必需 Policy 缺失或保守化、Server 未启用、实时能力发现失败、Schema Hash 不同、缺少必需工具或只读 dry-run 失败，Pack 验证都会 Fail Closed。

## 启用顺序

1. 部署 Migration 并确认 API/Worker 健康。
2. 部署并限制 Collector，验证 Readiness 与 ServiceAccount。
3. 注册并启用 MCP Server，再通过已认证管理 API 持久化全部固定只读 Tool Policy。
4. 管理员打开 **设置 → 运行时 → feature_flags**，设置 `enable_ops_patrol=true`。启用 DB AppConfig 后，仅修改 `api/config.yaml` 不会覆盖已有数据库行。
5. 保持 `enable_ops_patrol_fixture_replay=false`。
6. 创建 Pack 并检查实时 Preflight 摘要。向导会自动激活验证成功的 Pack；失败时保持非 Active，修复并重新验证后再显式激活。
7. 触发一次手动 Run，核验证据后再启用计划。

Pack 计划使用五段每日 Cron 与 IANA 时区。配置变更会增加 Pack Version、暂停其 ScheduledJob，并要求重新验证/激活。

## 必需配置

| 配置 | 归属与限制 |
|------|------------|
| `feature_flags.enable_ops_patrol` | DB 承载的全局 AppConfig 总开关 |
| `feature_flags.enable_ops_patrol_fixture_replay` | 仅测试/演示；生产保持 false |
| `patrol_retention.run_days` / `finding_days` | 默认 30 天，收敛到 1–90 |
| `patrol_retention.collector_evidence_days` | 默认 7 天，收敛到 1–90 |
| `patrol_retention.cleanup_batch_size` | 默认 100，每 Tick 收敛到 1–1000 |
| `AUDIT_SIGNING_KEY` | HMAC Key；随审计 Key ID 与历史 Key Map 轮换 |
| `OPS_COLLECTOR_*` | Collector 独有目标、白名单、注册表、并发与输出上限 |

Collector 变量与 Kubernetes 身份属于其部署，不属于 Pack Tool 参数；不得向 Pack 增加原始目的地。

## 验证

```bash
make test-patrol
helm lint deploy/helm/opencitadel
helm template opencitadel deploy/helm/opencitadel \
  --set opsCollector.enabled=true >/dev/null
kubectl kustomize deploy/kustomize/ops-collector >/dev/null
docker compose --env-file .env.example config --quiet
```

部署后执行：

```bash
kubectl -n opencitadel rollout status deployment/opencitadel-ops-collector
kubectl auth can-i get pods \
  --as system:serviceaccount:opencitadel:opencitadel-ops-collector
kubectl auth can-i create pods \
  --as system:serviceaccount:opencitadel:opencitadel-ops-collector
kubectl auth can-i get secrets \
  --as system:serviceaccount:opencitadel:opencitadel-ops-collector
```

`get pods` 应成功，`create pods` 与 `get secrets` 必须输出 `no`。

破坏性 Fixture 仅能用 `./scripts/run-patrol-fixtures.sh` 运行。它会创建专用 `kind-opencitadel-patrol-*` 集群、校验每次重置基线与写权限拒绝、评分全部 20 案例，并在结束时删除集群；仅显式设置 `PATROL_KEEP_DEMO_CLUSTER=true` 才保留。

## 证据验证

下载 Run 会写入 `patrol_evidence_downloaded` 审计动作。ZIP 包含 Session 审计材料以及：

- `patrol/run.json`、`patrol/pack-snapshot.json`
- `patrol/check-results.json`、`patrol/findings.json`
- `patrol/report.md`、`patrol/evidence-index.json`
- `manifest.json`、`chain-signature.txt`

信任证据前必须逐项校验 `manifest.json.file_hashes`。在受保护运维 Shell 中计算 `HMAC-SHA256(AUDIT_SIGNING_KEY, manifest.json 原始精确字节)`，并与 `chain-signature.txt` 中 `manifest HMAC-SHA256:` 后的值比较。根据 Manifest 的 `signing_key_id` 选择当前或保留的历史 Key。绝不能把签名 Key 发送到浏览器或通用在线验证服务。

## 保留、备份与恢复

Worker Scheduler 通过有租约的有界 Tick 清空过期证据引用并删除过期 Finding/Run；审计链行始终保留。若 Scheduler 被关闭，保留任务也不会运行，因此应监控 Scheduler Leader 与 Cleanup Log。

PostgreSQL 与对象存储备份遵循主[生产部署手册](deployment.zh-CN.md)。Patrol 表随数据库备份。恢复时应从同一恢复点恢复数据库、对象存储、当前/历史审计签名 Key 和 MCP/Collector 配置，再验证 Pack 后恢复计划。

回滚时不要删除 Patrol 表。安全停止方式是关闭功能总开关；有权限的历史仍可读取。

## 恢复与回滚

1. 关闭 `feature_flags.enable_ops_patrol`，停止新工作但保留数据。
2. 若只有单个目标异常，暂停对应 Pack。
3. 恢复 Collector 连通性或注册目标；不得临时放开任意目的地。
4. 重新验证所有变更过的 Pack；激活与版本绑定。
5. 打开总开关，先手动运行并检查证据，再恢复计划。

## 排障

| 现象 | 检查项 |
|------|--------|
| 导航缺失 / API 提示 disabled | DB 全局 AppConfig `feature_flags.enable_ops_patrol`；配置种子不会覆盖已有行 |
| 向导看不到 Collector | MCP Server 已启用、在当前工作区 Scope 可访问且使用 Streamable HTTP |
| `COLLECTOR_UNAVAILABLE` | MCP URL、Service/NetworkPolicy、Readiness、DNS、最近成功 Preflight |
| `COLLECTOR_CAPABILITY_MISMATCH` | Collector Image/Tool Schema 变化；暂停并重新验证 Pack |
| `TARGET_SCOPE_DENIED` | Target Ref、Namespace/Workload/注册目标白名单 |
| `AUTH_FAILED` | P0 注册 HTTP 探针不接受任意 Auth Header；使用获批内网 Status Endpoint 或禁用检查，严禁在 URL 嵌入凭据 |
| `RATE_LIMITED` | Collector 只重试一次；降低计划并发或上游负载 |
| `EVIDENCE_INCOMPLETE` | 必需证据类型、规范化 SHA-256、过期时间和截断 |
| Pack 无法激活 | 当前 Pack Version 未成功验证，或 Capability/Tool Policy 校验失败 |
| Run 长期 queued/running | Worker/Redis、模型可用性、活动 Run 锁、Pack Timeout（默认 15 分钟，最大 30 分钟） |
| 无计划 Run | Pack Active、计划 Enabled、Scheduler Enabled/Leader、时区/Next Run、总开关仍开启 |
| 保留任务不前进 | Worker Scheduler Loop、Leader Lease、`patrol_retention` 限制与 Cleanup Log |

日志应包含 Run、Pack、Session、Check、Request、Target 标识和状态/错误码，但不能包含凭据或原始 Authorization Header。
