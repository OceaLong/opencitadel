# Plan D · 部署一致性与可观测性（Phase 3，预估 3–5 天）

前置：Plan A 完成（A-3 密钥、A-7 acceptance）。本方向大部分改动在 `deploy/helm/` 与 CI，代码面小。

## D-1 helm 安装即坏项（高优）

1. **补 `SANDBOX_TOKEN_SEED`**：生产模式必填 ≥32 字符（`api/core/config.py:294-299`），helm 全线缺失 → API Pod 启动即 ValueError。`values.yaml` 加 `secrets.sandboxTokenSeed`、`templates/secret.yaml` 落键、`values.schema.json` 进 required。
2. **镜像默认值**：`values.yaml:5-20` 的 `repository: opencitadel-api`/`tag: latest` 拉不到；改为 `ghcr.io/ocealong/opencitadel-*` + `tag: ""` fallback 到 `.Chart.AppVersion`，release 流程（`.github/workflows/release.yml`）同步 bump appVersion。
3. **`TRUSTED_PROXY_CIDRS` 默认值**：`values.yaml:290` 的 `10.0.0.0/8` 过不了 HEAD 新增校验；改为空串（禁用信任）并在注释指导按 ingress pod CIDR 精确配置，schema 加 pattern 校验。
4. **ops-collector/actuator token 条件必填**：`values.yaml:46,75` 默认空串，`enabled=true` 时服务端强制 ≥32 字符即 CrashLoop；`values.schema.json` 用条件 schema 把 token 设为 enabled 时必填 `minLength: 32`。
5. **schema 左移**：`values.schema.json` 对全部 secrets 加 `minLength: 32` + 拒绝 `replace-with-*`/`change-me` 占位（对照 `api/core/config.py` `_PLACEHOLDER_MARKERS`），把失败从 Pod 启动左移到 `helm install`。

**验收**：`helm template` + `helm install --dry-run` 用默认 values 报出全部必填缺失；填好后 kind 集群安装一次起成。

## D-2 helm 与 compose 安全/健壮性对齐

1. **Ingress TLS + 安全头**：`templates/ingress.yaml` 加 `tls` 块与默认 annotations（HSTS/CSP/nosniff/Referrer-Policy），CSP 值抽成 values 单一变量，与 `nginx/templates/*.conf.template` 共用一份内容，杜绝双份漂移。
2. **UI 探针**：`deployment-ui.yaml` 补 readiness/liveness（compose 侧已有 healthcheck）。
3. **selectorLabels**：`_helpers.tpl` 加含 `app.kubernetes.io/instance: {{ .Release.Name }}` 的 selector helper，替换 `deployment-api.yaml:10`、`deployment-ui.yaml:11`、`statefulset-postgres.yaml:10` 及 NetworkPolicy 的裸 component 选择器（现状：同 ns 双 release 互抢 Pod）。
4. **API 的 k8s sandbox RBAC**：`SANDBOX_DRIVER=kubernetes` 时 API 进程代码路径可直连 k8s driver（`factory.py:111-143`）但无 ServiceAccount——给 api 建同 kernel 的 SA+Role，或加 contract 测试断言 API 永不直连 driver（二选一，先确认运行时是否真的会走到）。
5. **bootstrap 并发锁**：`bootstrap_service.py:41-56`、`skill_service.py:326-356` 的 read-then-write 包进 `pg_advisory_lock`（复用 `migrate.py:26-50` 现成模式）或改 `ON CONFLICT DO NOTHING`，消除 `replicaCount.api: 2` 竞态。

**验收**：kind 双 release 同 ns 安装互不干扰；api 2 副本滚动重启无 bootstrap 报错；ingress 响应头与 compose nginx 一致。

## D-3 可观测性闭环

1. **metrics 采集**：helm 加 `monitoring.serviceMonitor.enabled` 模板（bearer token 引用 METRICS_TOKEN 的 secret）；`metricsToken` 默认自动生成（helm `randAlphaNum` + 既有 secret 保留逻辑）而非空串 404（`metrics_routes.py:33-40` fail-closed 行为保留）。
2. **compose 侧可选监控 profile**：新增 `monitoring` profile（prometheus + grafana 最小配置，抓 `/api/metrics`），本地也能看到 PrometheusRule 里那 7 条告警对应的指标。
3. **OTel 文档化**：`OTEL_ENABLED/SERVICE_NAME/EXPORTER_ENDPOINT` 进 `.env.example`（A-6 已列，这里补 helm values 对应项）。
4. **ops token 语义收敛**：`settings.ops_actuator_token` 全仓零读取（`core/config.py:89`）——删除并在文档写明"actuator 鉴权头在 MCP Server 注册页配置"，或接进 actuator 自动注册与 collector 对称（推荐后者，行为对称好理解）。

**验收**：kind + prometheus-operator 下 ServiceMonitor 抓到指标、告警规则可评估；compose `--profile monitoring` 起来后 grafana 有数据。

## D-4 数据与迁移门禁

1. **迁移 drift 门禁**：CI 加 `alembic check`（autogenerate 空 diff）步骤，防止模型改动绕过迁移（现状 `0001greenfield_initial.py:99-101` 用 `metadata.create_all()`，一旦有真实用户无升级路径）。发布 v1 前把 0001 冻结为显式 DDL。
2. **alembic offline 模式修复**：`env.py:42-64` offline 分支补三个 `set_config` GUC 注入，或显式 `raise NotImplementedError`（现状生成的 SQL 会在 `_assert_runtime_roles` RAISE）。
3. **存量卷升级路径**：postgres init 脚本只在空数据目录执行，已有卷拿不到新加的 pg_trgm——升级文档补手工 `CREATE EXTENSION` 步骤，或迁移容器用 admin 连接预建扩展（推荐后者，自动化）。
4. **compose 备份方案**：helm 已有 `cronjob-postgres-backup`（默认关），compose 侧零方案——加 `scripts/backup.sh`（pg_dump + minio 数据）与文档，或 compose `backup` profile 定时容器。
5. **compose 小卫生**：nginx 无条件挂载 `/etc/letsencrypt:ro`（非 Linux 机器会被 docker 建出 root 空目录）→ 移到 https override 文件；`STORAGE_PROVIDER=minio` 但 profile 未开时无启动期报错 → api 启动校验对 `MINIO_ENDPOINT` 做连通性 fail-fast。
6. **CORS 语义**：`CORS_ORIGINS="*"` 实际是"禁用跨域"（`main.py:98-103` allow_all → 空列表）——默认值改空串+注释说明，或对 `*`+credentials 组合直接报错。

**验收**：CI 迁移门禁生效（故意改模型不写迁移 → CI 红）；从零与存量卷两条升级路径均文档化并演练一次；备份脚本能恢复。

## D-5 测试口径统一

- `make test` 聚合全部子套件（当前只跑 api+ui，漏 sandbox / ops-collector / ops-actuator / quality-check，`Makefile:32-53`）。
- 新增 `make test-api-strict`：显式导出 `OPENCITADEL_REQUIRE_POSTGRES_TESTS=1` 等 REQUIRE 变量，杜绝"依赖不可用静默 skip 造成的假绿"（`api/tests/conftest.py:54-61,94-100`）。

**验收**：CI 与本地 `make test` 覆盖集一致；strict 模式在无 Postgres 时明确失败而非跳过。

## 执行顺序建议

D-1（解阻 helm）→ D-2 → D-4 → D-3 → D-5。helm 改动用 kind 验证（e2e 已有 kind fixture 可复用）；每项完成后暂存+patch 备份。
