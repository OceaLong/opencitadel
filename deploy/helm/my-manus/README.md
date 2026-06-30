# MyManus Helm Chart

Kubernetes 部署 MyManus 的 Helm Chart，支持 API 与 Agent Worker 独立扩缩容。

## 前置要求

- Kubernetes 1.24+
- Helm 3.x
- 已构建并推送 `manus-api` 与 `manus-worker` 镜像（`api/Dockerfile` 多阶段 target）
- 集群内可访问 PostgreSQL（pgvector）、Redis

## 安装

```bash
helm upgrade --install my-manus ./deploy/helm/my-manus \
  --namespace manus --create-namespace \
  --set image.api.repository=your-registry/manus-api \
  --set image.api.tag=latest \
  --set image.worker.repository=your-registry/manus-worker \
  --set image.worker.tag=latest \
  --set replicaCount.api=2 \
  --set replicaCount.worker=2
```

## 主要 Values

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `replicaCount.api` | 2 | API 副本数 |
| `replicaCount.worker` | 2 | Worker 副本数 |
| `autoscaling.api.enabled` | true | API HPA |
| `autoscaling.worker.enabled` | true | Worker HPA |
| `migrate.enabled` | true | API initContainer 执行迁移 |
| `env` | 见 values.yaml | 非敏感环境变量（DB/Redis 主机、日志级别等） |
| `secrets` | 见 values.yaml | 敏感配置，渲染为 Secret 并通过 `envFrom` 注入 |
| `appConfig` | 见 values.yaml | 应用行为配置，渲染为 ConfigMap 并挂载为 `/app/config.yaml` |

> **注意**：生产部署前请通过 `--set` 或独立 values 文件覆盖 `secrets.apiKeySecret`、`secrets.postgresPassword` 等敏感项，并确认 `env.POSTGRES_HOST`、`env.REDIS_HOST` 指向集群内实际服务地址。OpenTelemetry 等行为开关通过 `appConfig.observability` 管理。沙箱执行（`sandbox.address` / docker.sock）在 K8s 中需按 [docs/architecture-evolution.md](../../../docs/architecture-evolution.md) 外置配置。

## 架构

- **API Deployment**：无状态 FastAPI，SSE 连接层
- **Worker Deployment**：消费 Redis dispatch 队列，执行 Agent
- **migrate initContainer**：`python -m app.migrate`，与 docker-compose `manus-migrate` 等价

## 扩缩容

```bash
# 手动调整 Worker 副本（处理 Agent 负载）
kubectl scale deployment my-manus-worker --replicas=4 -n manus

# 或启用 HPA（values.yaml 中 autoscaling.worker.enabled=true）
```

## 架构演进

单机 Compose 稳定后，按阶段拆分计算与沙箱执行面，详见 [docs/architecture-evolution.md](../../../docs/architecture-evolution.md)。

推荐演进顺序：

1. PostgreSQL / Redis 外置（释放主节点内存）
2. 本 Chart 部署 API + Worker（HPA 按队列深度或 CPU 扩缩）
3. `sandbox.address` 指向远程沙箱集群（Worker 不再挂载 docker.sock）

## 相关文档

- 根目录 [README.md](../../README.md) — 架构与配置说明
- [DEPLOYMENT.md](../../DEPLOYMENT.md) — 生产部署指南
- [docs/architecture-evolution.md](../../../docs/architecture-evolution.md) — 扩容与沙箱外置
- [api/README.md](../../api/README.md) — API / Worker 本地开发
