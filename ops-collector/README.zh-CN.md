[English](README.md)

# OpenCitadel Ops Collector

Ops Collector 是为 Ops Patrol 独立部署的只读 MCP 服务。它仅暴露九个固定操作，接收已注册标识符，不接受原始 PromQL、URL、SQL、Kubernetes 路径或 Shell 命令。

## 操作

| Tool | 上游访问 | 有界输入 |
|------|----------|----------|
| `get_capabilities` | 无 | 返回 Tool/Schema/Capability Hash |
| `k8s_workload_summary` | Kubernetes 只读 API | 白名单 Namespace 与 Workload |
| `k8s_recent_events` | Kubernetes 只读 API | 白名单 Namespace、时间窗与数量 |
| `k8s_pod_logs` | Kubernetes Pod Log | 白名单 Namespace、有界 Tail/时间窗 |
| `prom_query` | Prometheus HTTP API | 仅已注册 Query ID |
| `http_probe` | HTTP | 仅已注册 Probe ID |
| `certificate_status` | TLS | 仅已注册 HTTPS Probe ID |
| `backup_status` | 备份状态 Endpoint | 仅已注册 Backup ID；不读取备份内容 |
| `dependency_status` | TCP 连通性 | 仅已注册 Dependency ID |

每个操作均声明为只读、非破坏、幂等且 Closed-world。响应使用统一 Envelope，包含 `target_ref`、状态、耗时、有界数据、证据引用、警告和稳定错误码。

## 配置参考

配置仅来自环境变量，统一使用 `OPS_COLLECTOR_` 前缀；结构化值使用 JSON。

| 变量 | 默认值 / 范围 | 作用 |
|------|---------------|------|
| `TARGET_REF` | `opencitadel-local` | 与 Pack 匹配的稳定目标身份 |
| `ALLOWED_NAMESPACES` | `["opencitadel"]` | 非空 Namespace 白名单 |
| `ALLOWED_WORKLOADS` | `{}` | Namespace 到 Workload ID 白名单的 JSON Map；空 List 表示允许该 Namespace 内所有 Workload |
| `PROMETHEUS_QUERIES` | `{}` | Query ID 到 `base_url`、固定 `promql` 与可选超时的 Map |
| `HTTP_PROBES` | `{}` | Probe ID 到 `url`、期望状态码列表与可选超时的 Map |
| `CERTIFICATE_PROBES` | `{}` | Probe ID 到 HTTPS `url` 与可选超时的 Map |
| `BACKUPS` | `{}` | Backup ID 到 `status_url` 与可选超时的 Map |
| `DEPENDENCIES` | `{}` | ID 到单个或一组 `{kind,host,port,timeout_seconds}` 目标 |
| `TRANSPORT` | `streamable-http` | `streamable-http` 或仅开发使用的 `stdio` |
| `ALLOW_STDIO` | `false` | 启动 stdio 前还必须显式设为 true |
| `HOST` / `PORT` | `0.0.0.0` / `8090` | Streamable HTTP 监听地址（`/mcp`） |
| `CONCURRENCY` | `4`，范围 1–8 | 最大并发探针数 |
| `MAX_OUTPUT_BYTES` | `65536`，最大 1 MiB | 序列化响应上限 |
| `MAX_ROWS` | `200`，最大 1000 | 表格/采样行上限 |
| `MAX_ARRAY_ITEMS` | `200`，最大 1000 | 单个数组元素上限 |
| `MAX_STRING_CHARS` | `32768`，最大 131072 | 单个字符串上限 |

完整变量名为前缀加表中名称，例如 `OPS_COLLECTOR_HTTP_PROBES`。

Helm Values 使用原生 YAML 对象，并在模板中渲染为 JSON：

```yaml
opsCollector:
  enabled: true
  targetRef: production-cluster-a
  allowedNamespaces: [opencitadel]
  allowedWorkloads:
    opencitadel: [opencitadel-api, opencitadel-worker]
  registeredPrometheusQueries:
    app-5xx-ratio:
      base_url: http://prometheus.monitoring.svc:9090
      promql: sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))
  registeredHttpProbes:
    primary-endpoint:
      url: https://opencitadel.example.com/api/status
      expected_statuses: [200]
  registeredCertificateProbes:
    primary-tls:
      url: https://opencitadel.example.com
  registeredBackups:
    primary-database:
      status_url: https://backup-status.example.internal/latest
  registeredDependencies:
    primary-dependencies:
      - {kind: postgres, host: postgres.database.svc, port: 5432}
      - {kind: redis, host: redis.cache.svc, port: 6379}
```

内置 Kubernetes Baseline 会引用 `pvc-utilization`、`app-5xx-ratio`、`primary-tls`、`primary-database`、`primary-dependencies` 与 `primary-endpoint`。UI 向导会启用全部 Baseline 检查，因此验证前必须注册每个 ID；自定义 API 客户端也可以提交禁用部分检查的完整 Pack Config。

## 本地运行

默认使用 Streamable HTTP（`/mcp`，端口 `8090`）：

```bash
docker compose --profile patrol up -d --build opencitadel-ops-collector
```

Compose Profile 适合验证传输、配置和非 Kubernetes 注册探针；它不会挂载宿主机 kubeconfig。真实 Kubernetes 观察应使用 Helm/Kustomize ServiceAccount 部署，不应增加高权限宿主凭据挂载。

仅开发使用的 stdio：

```bash
OPS_COLLECTOR_ALLOW_STDIO=true uv run opencitadel-ops-collector --transport stdio
```

生产部署严禁启用 stdio。

## Kubernetes 部署

- Helm：设置 `opsCollector.enabled=true`，并在 `deploy/helm/opencitadel/values.yaml` 配置注册表。
- Kustomize：以 `deploy/kustomize/ops-collector` 为 Base，Patch 镜像、Target Ref、白名单和注册目标环境变量。
- Service 保持 `ClusterIP`，仅允许 API/Worker 访问 8090。
- 保持只读 ServiceAccount；其权限排除 Secret、exec、attach、impersonation 以及全部变更动词。
- 根据 Kubernetes API 和注册目标的准确位置复核 NetworkPolicy Egress。注册表是应用层 SSRF 权威边界，NetworkPolicy 作为纵深防御。

容器使用 UID/GID 10001、只读根文件系统、删除全部 Linux Capability、`RuntimeDefault` seccomp，并仅提供有界可写 `/tmp`。

## 认证与数据处理

Kubernetes 访问使用 Pod ServiceAccount，该凭据不会成为 Tool 参数或响应字段。Developer Preview 不接受注册 Prometheus/HTTP/备份探针的任意 Authorization Header。应使用不需要应用凭据、仅返回最少数据的内网 Status Endpoint，或禁用对应检查；严禁把凭据写入注册 URL。

Collector 会在应用输出上限前脱敏 Authorization 形态值、密码、API Key、Token、连接串、Cookie、JWT 形态文本和 Secret 形态对象字段。不能只依赖脱敏：状态响应应保持最小化，Collector 绝不能暴露到公网。

## 开发与验证

```bash
uv sync --frozen
uv run pytest -q
```

破坏性黄金集实验仅可在仓库根目录运行 `./scripts/run-patrol-fixtures.sh`。参见 `deploy/patrol-demo/README.zh-CN.md`，严禁将 Fixture 应用于共享集群。

## 相关文档

- [Ops Patrol 架构](../docs/architecture/ops-patrol.zh-CN.md)
- [Ops Patrol 运维](../docs/operations/ops-patrol.zh-CN.md)
- [运行 Patrol](../docs/tutorials/06-ops-patrol.zh-CN.md)
