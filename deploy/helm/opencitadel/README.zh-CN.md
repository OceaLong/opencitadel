[English](README.md)

# OpenCitadel Helm Chart

Kubernetes 部署 OpenCitadel 的 Helm Chart，支持 API 与 Agent Worker 独立扩缩容。

## 前置要求

- Kubernetes 1.24+
- Helm 3.x
- 已构建并推送 `opencitadel-api` 与 `opencitadel-worker` 镜像（`api/Dockerfile` 多阶段 target）
- 集群内可访问 PostgreSQL（pgvector）、Redis

## 安装

```bash
helm upgrade --install opencitadel ./deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --values production-values.yaml \
  --set image.api.repository=your-registry/opencitadel-api \
  --set image.worker.repository=your-registry/opencitadel-worker
```

完整镜像构建/推送步骤与生产环境 `--set` 参数（六个镜像，含可选 Ops Collector、ingress、sandbox
driver）：见[部署指南](../../../docs/operations/deployment.zh-CN.md)的
Kubernetes / Helm 部署一节。

### local 模式（集群内 MinIO）

```bash
helm upgrade --install opencitadel ./deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --set minio.enabled=true \
  --set env.STORAGE_PROVIDER=minio \
  --set secrets.minioAccessKey=minioadmin \
  --set secrets.minioSecretKey=minioadmin
```

`minio.enabled=true` 时 Chart 自动部署 MinIO StatefulSet 并将 `MINIO_ENDPOINT` 指向集群内 Service。

## 主要 Values

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `replicaCount.api` | 2 | API 副本数 |
| `replicaCount.worker` | 2 | Worker 副本数 |
| `autoscaling.api.enabled` | true | API HPA |
| `autoscaling.worker.enabled` | true | Worker HPA |
| `migrate.enabled` | true | API initContainer 执行迁移 |
| `postgresql.adminUser` | postgres | 仅用于初始化的 PostgreSQL 管理角色 |
| `postgresql.user` | opencitadel_app | 受 RLS 约束的非超级用户应用/迁移角色 |
| `minio.enabled` | false | 集群内 MinIO（local 模式设为 true） |
| `minio.storage` | 20Gi | MinIO PVC 大小 |
| `opsCollector.enabled` | false | 部署可选的固定只读 Patrol Collector |
| `opsCollector.image.*` | 见 values.yaml | Collector Repository、Tag 与 Pull Policy |
| `opsCollector.allowedNamespaces` / `allowedWorkloads` | 受限默认值 | Kubernetes Scope 白名单 |
| `opsCollector.registered*` | `{}` | 注册的 Prometheus/HTTP/TLS/备份/依赖目标 |
| `env.STORAGE_PROVIDER` | cos | 对象存储后端：`cos` 或 `minio` |
| `env` | 见 values.yaml | 非敏感环境变量（DB/Redis 主机、日志级别等） |
| `secrets` | 见 values.yaml | 敏感配置，渲染为 Secret 并通过 `envFrom` 注入 |
| `appConfig` | 见 values.yaml | 应用行为配置，渲染为 ConfigMap 并挂载为 `/app/config.yaml` |

> **注意**：生产部署前请通过 `--set` 或独立 values 文件覆盖全部敏感项：
> `secrets.apiKeySecret`、`secrets.auditSigningKey`、`secrets.jwtSecret`、`secrets.sessionSecret`、
> `secrets.bootstrapAdminPassword`、`secrets.postgresAdminPassword`、`secrets.postgresPassword`
> 与 `secrets.redisPassword`。两个 PostgreSQL 密码必须不同。
> 将 `env.FRONTEND_BASE_URL`、`env.OAUTH_REDIRECT_BASE` 与 `env.COOKIE_SECURE=true` 设置为与 Ingress 域名一致。
> Helm 部署默认 `env.USE_DB_APP_CONFIG="true"`。确认 `env.POSTGRES_HOST`、`env.REDIS_HOST` 指向集群内实际服务。
> 若生产应用数据库角色是超级用户或具有 `BYPASSRLS`，应用会拒绝启动。

## 生产安全要求

- `secrets.apiKeySecret`、`secrets.auditSigningKey`、`secrets.jwtSecret`、
  `secrets.sessionSecret`、`secrets.bootstrapAdminPassword`、
  `secrets.postgresAdminPassword`、`secrets.postgresPassword`、
  `secrets.redisPassword` 的长度与互异性要求见[部署指南 — local
  模式](../../../docs/operations/deployment.zh-CN.md#local-模式配置)（该规则对
  cloud 与 local 两套 `.env` 模板均适用）；轮换时同步设置 key id 与
  previous-key JSON Map。
- API、Worker、迁移 initContainer 都使用 `postgresql.user`，这是受
  RLS 约束的非超级用户。生产启动会拒绝 `rolsuper=true` 或
  `rolbypassrls=true`。
- 保持 `networkPolicy.enabled=true`。它只允许 API/Worker 访问沙箱，
  沙箱出站仅放行 DNS 与公网地址段，阻断私网、Link-local、Metadata 与
  保留地址。
- 设置 `env.COOKIE_SECURE=true`、公网 HTTPS 前端/OAuth URL，并按同一指南
  设置 `env.TRUSTED_PROXY_CIDRS` / `env.OUTBOUND_ALLOWED_PORTS` /
  `env.OUTBOUND_PRIVATE_HOST_ALLOWLIST`。
- `production-values.yaml` 应存放在批准的加密 Secret 机制中，不得提交。

## 已有 Chart 托管 PostgreSQL PVC

`/docker-entrypoint-initdb.d` 只在全新数据目录执行。已有 PVC 切换到应用
角色前，进入维护窗口并在仓库根目录执行：

```bash
NS=opencitadel
RELEASE=opencitadel
CHART=./deploy/helm/opencitadel
VALUES=production-values.yaml
APP_USER=opencitadel_app
PG_POD="$(kubectl -n "$NS" get pod \
  -l app.kubernetes.io/component=postgres \
  -o jsonpath='{.items[0].metadata.name}')"

# 1. 停止写入并备份当前数据库。
kubectl -n "$NS" scale deployment \
  "${RELEASE}-api" "${RELEASE}-worker" --replicas=0
kubectl -n "$NS" exec "$PG_POD" -- sh -ceu \
  'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "${RELEASE}-before-app-role-$(date +%Y%m%d%H%M%S).sql"

# 2. 应用新 Secret 与脚本 ConfigMap，但不启动迁移。
helm template "$RELEASE" "$CHART" \
  --namespace "$NS" --values "$VALUES" \
  --show-only templates/secret.yaml \
  --show-only templates/configmap-postgres-init.yaml \
  | kubectl -n "$NS" apply -f -

# 3. 复制仓库内的原始脚本，不要粘贴修改版。
kubectl -n "$NS" cp \
  deploy/helm/opencitadel/files/postgres/init-app-role.sh \
  "$PG_POD:/tmp/init-app-role.sh"
APP_PASSWORD="$(kubectl -n "$NS" get secret "${RELEASE}-secret" \
  -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 --decode)"
kubectl -n "$NS" exec "$PG_POD" -- chmod 0500 /tmp/init-app-role.sh

# 4. 创建/收敛不可绕过 RLS 的应用角色，并转移关系对象所有权。
kubectl -n "$NS" exec "$PG_POD" -- env \
  OPENCITADEL_APP_USER="$APP_USER" \
  OPENCITADEL_APP_PASSWORD="$APP_PASSWORD" \
  /tmp/init-app-role.sh

# 5. rolsuper、rolbypassrls 必须为 false，wrong_owner 必须为 0。
kubectl -n "$NS" exec -i "$PG_POD" -- env \
  OPENCITADEL_APP_USER="$APP_USER" sh -ceu '
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -v app_user="$OPENCITADEL_APP_USER"
  ' <<'SQL'
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname = :'app_user';
SELECT count(*) AS wrong_owner
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
  AND pg_get_userbyid(c.relowner) <> :'app_user';
SQL
unset APP_PASSWORD

# 6. 角色/所有权校验通过后再升级。
helm upgrade "$RELEASE" "$CHART" \
  --namespace "$NS" --values "$VALUES"
```

该流程仅适用于 Chart 托管 StatefulSet。外部 PostgreSQL 应通过 Provider
批准的管理通道执行
`deploy/helm/opencitadel/files/postgres/init-app-role.sh`，再允许 Helm
启动 migration initContainer。

## 生产验证

```bash
# NetworkPolicy 必须可渲染且已存在。
helm template opencitadel ./deploy/helm/opencitadel \
  --namespace opencitadel --values production-values.yaml \
  --show-only templates/networkpolicy-sandbox.yaml
kubectl -n opencitadel get networkpolicy opencitadel-sandbox

# 迁移必须先完成，API/Worker 随后可用。
kubectl -n opencitadel rollout status deployment/opencitadel-api
kubectl -n opencitadel rollout status deployment/opencitadel-worker
kubectl -n opencitadel exec deployment/opencitadel-api -- \
  curl --fail http://127.0.0.1:8000/api/status
```

每次数据库角色变更后重复 `rolsuper`、`rolbypassrls`、`wrong_owner`
查询。加密/审计 Key 轮换与链校验见[生产部署
指南](../../../docs/operations/deployment.zh-CN.md#凭证加密与审计签名-key-轮换)。

## Release 镜像

打 tag（`v*`）后，[`.github/workflows/release.yml`](../../../.github/workflows/release.yml) 会发布多架构镜像到 `ghcr.io/ocealong/opencitadel-{api,worker,migrate,ui,sandbox,ops-collector}`。通过 `image.*` 与 `opsCollector.image.*` 引用 Release 构建。

## 架构

- **API Deployment**：无状态 FastAPI，SSE 连接层
- **Worker Deployment**：消费 Redis dispatch 队列，执行 Agent
- **migrate initContainer**：`python -m app.migrate`，与 docker-compose `opencitadel-migrate` 等价

## 扩缩容

```bash
# 手动调整 Worker 副本（处理 Agent 负载）
kubectl scale deployment opencitadel-worker --replicas=4 -n opencitadel

# 或启用 HPA（values.yaml 中 autoscaling.worker.enabled=true）
```

## 架构演进

单机 Compose 稳定后，按阶段拆分计算与沙箱执行面，详见 [架构演进指南](../../../docs/architecture/architecture-evolution.zh-CN.md)。

推荐演进顺序：

1. PostgreSQL / Redis 外置（释放主节点内存）
2. 本 Chart 部署 API + Worker（HPA 按队列深度或 CPU 扩缩）
3. `sandbox.address` 指向远程沙箱集群（Worker 不再挂载 docker.sock）

## 相关文档

- 根目录 [README.md](../../../README.zh-CN.md) — 架构与配置说明
- [生产部署指南](../../../docs/operations/deployment.zh-CN.md) — 生产部署指南
- [Ops Patrol 运维](../../../docs/operations/ops-patrol.zh-CN.md) — Collector Values、Policy、验证与恢复
- [架构演进指南](../../../docs/architecture/architecture-evolution.zh-CN.md) — 扩容与沙箱外置
- [api/README.zh-CN.md](../../../api/README.zh-CN.md) — API / Worker 本地开发
