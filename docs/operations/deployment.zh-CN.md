# 部署 OpenCitadel v2

[English](deployment.md)

## 拓扑

生产环境运行 API、执行内核、UI、PostgreSQL/pgvector、Redis、对象存储、沙箱出站
代理和沙箱生命周期实现。Compose 使用带认证的 Docker Broker；Helm 让内核通过
Namespace 级 ServiceAccount 创建按 Run 隔离的 Pod。

不存在原地数据升级。先备份仍需保留的数据，再部署空 PostgreSQL 数据库，并且只运行
`0001greenfield`。

## 必要控制

- 为 `API_KEY_SECRET`、`AUDIT_SIGNING_KEY`、`JWT_SECRET`、`SESSION_SECRET`、
  `DATABASE_AUTHORIZATION_SIGNING_SECRET`、`SANDBOX_BROKER_TOKEN` 和
  `SANDBOX_TOKEN_SEED` 分别生成独立值。
- PostgreSQL 管理、迁移、API、内核凭据必须各不相同。
- 配置 `REDIS_PASSWORD`、对象存储凭据和高强度初始管理员密码。
- 保持 `COOKIE_SECURE=true`，准确设置 `FRONTEND_BASE_URL` 与
  `OAUTH_REDIRECT_BASE`。
- `TRUSTED_PROXY_CIDRS` 只信任精确的反向代理网段。
- 最小化 `OUTBOUND_ALLOWED_PORTS` 和私网主机白名单。
- 只有 Broker 能挂载 Docker Socket；API 和内核不得挂载。

复制 `.env.example`、替换全部占位值，然后执行：

```bash
docker compose --profile local build opencitadel-sandbox
docker compose --profile local up -d --build
curl -fsS http://localhost:8088/api/health/ready
```

如需明确销毁本地 v2 数据并从空库启动：

```bash
bash scripts/quickstart.sh --reset-data
```

该命令只删除选定 Compose Project 的容器和命名卷。不要使用全宿主机 Docker Prune
充当 OpenCitadel 重置。

## Kubernetes

```bash
helm lint deploy/helm/opencitadel --set-file secrets=production-secrets.yaml
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  -f production-values.yaml
```

迁移 Init Container 必须在 API Ready 前完成。确认内核 ServiceAccount 只能管理发布
Namespace 中的 Pod。保持沙箱 NetworkPolicy 开启：沙箱只允许 DNS 和出站代理；可信
内核可访问沙箱 API/CDP 端口。

## 验证与恢复

- API 生命周期：`/api/health/live`、`/api/health/ready`。
- 内核生命周期：`python -m app.execution_kernel_health readiness`。
- Projection 漂移时从追加式 Journal 重建。
- PostgreSQL 备份是权威恢复资产；Redis 可以丢失。
- 通过新控制面记录轮换凭据；不要修改事件或审计历史。
