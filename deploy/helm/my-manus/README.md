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
| `env.OTEL_ENABLED` | false | OpenTelemetry（运行时开关实际以 `config.yaml` 的 `observability.otel_enabled` 为准） |

> **注意**：当前 Helm Chart 为骨架配置，尚未覆盖 Docker Compose 中的完整生产必需项（`API_KEY_SECRET`、数据库/Redis 连接、COS 密钥、`config.yaml` 挂载等）。生产环境请优先使用 Docker Compose，或自行补全 Helm Secrets 与 ConfigMap。

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

## 相关文档

- 根目录 [README.md](../../README.md) — 架构与配置说明
- [DEPLOYMENT.md](../../DEPLOYMENT.md) — 生产部署指南
- [api/README.md](../../api/README.md) — API / Worker 本地开发
