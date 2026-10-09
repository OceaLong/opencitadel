[English](ops-patrol.md)

# Ops Patrol 运维

Ops Patrol 完全通过统一执行内核运行。采集只读且判定确定；修复是独立、需要正式审批的
Run。系统不存在 Patrol 私有任务队列，也不存在模型控制的写路径。

## 生产就绪

- API、执行内核、PostgreSQL、Redis、Migration、对象存储与模型端点均健康。
- `AUDIT_SIGNING_KEY` 唯一且由 Secret 系统管理。
- Collector 与 Actuator 使用不同的 ServiceAccount 和 NetworkPolicy。
- 每个 Namespace、Workload、Query 与探针目标均已注册。
- Fixture Replay 保持关闭。
- 启用调度前，至少一个已验证 Pack 的手动 Patrol Run 成功。

## 安全边界

Collector 固定暴露九个工具：能力发现、Kubernetes Workload/Event/Log，以及注册的
Prometheus、HTTP、TLS、备份和依赖探针。它没有 Shell、Browser、Mutation API、
Raw URL 或 Raw PromQL 输入。所有返回字符串都视为不可信数据；服务端验证封闭 Schema
并自行执行确定性断言。

可选 Actuator 只为白名单 Deployment/StatefulSet 暴露注册的 Restart、Scale 与
Rollback 操作，且绝不暴露给模型。`remediation` Run 必须先持久化审批，
`remediation.execute` Activity 才能调用 Actuator。

两种部署均使用非 Root、只读 Root Filesystem、Capability Drop、受限 `/tmp` 和内部监听；Kubernetes 清单显式设置 `RuntimeDefault` Seccomp 与 ClusterIP，Compose 设置 `no-new-privileges` 且不发布宿主端口。

## 部署 Collector 与 Actuator

Compose 可验证 Transport 与非 Kubernetes 探针：

```bash
docker compose --profile patrol up -d --build opencitadel-ops-collector
docker compose --profile actuator up -d --build opencitadel-ops-actuator
```

先设置强且独立的 `OPS_COLLECTOR_TOKEN` / `OPS_ACTUATOR_TOKEN`（至少 32 字符），并在集成加密 Headers 中配置对应 `Authorization: Bearer <token>`。Helm Values 使用 `opsCollector.token` / `opsActuator.token`；Kustomize Base 的 Secret 默认空，必须填充。Compose Actuator 未注入 Workload 白名单，只支持传输/认证检查。

Compose 不挂载宿主 Kubernetes 凭证。真实集群检查必须通过 Helm 或 Kustomize 使用专用
ServiceAccount。

```bash
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --values values.production.yaml \
  --set opsCollector.enabled=true \
  --set opsCollector.targetRef=cluster-a \
  --set-json 'opsCollector.allowedNamespaces=["opencitadel"]'

# 只有需要审批修复时才启用。
helm upgrade opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --values values.production.yaml \
  --set opsActuator.enabled=true \
  --set opsActuator.targetRef=cluster-a \
  --set-json 'opsActuator.allowedNamespaces=["opencitadel"]' \
  --set-json 'opsActuator.allowedWorkloads={"opencitadel":{"opencitadel-api":{"kind":"deployment","min_replicas":2,"max_replicas":10}}}'
```

Kustomize 验证：

```bash
kubectl kustomize deploy/kustomize/ops-collector >/dev/null
kubectl kustomize deploy/kustomize/ops-actuator >/dev/null
```

应用 Base 前必须 Patch 镜像 Tag、Target Ref、白名单、注册目标 Map、Namespace、资源
限制与精确 Egress。Collector Ingress 只允许 API/执行内核访问 8090；Actuator Ingress
只允许 API/执行内核访问 8091。默认 Collector Egress 放行 DNS 以及任意目标的 443/5432/6379/9000/9090，Actuator 放行 DNS 以及任意目标的 443/6443；这些端口规则未限定 IP/Selector。应按真实 API Endpoint 和已注册探针收紧；Collector 默认未放行 8000/8090/8080 或 6443，内网 HTTP 探针和 DNAT 后的 API 端口可能需要调整。注册表与 RBAC 仍是应用/资源权限边界。

## 注册 MCP Server

在 **设置 → 集成** 中将 Collector 注册为 Streamable HTTP：

```text
http://opencitadel-ops-collector:8090/mcp
```

为每个 Collector 工具持久化保守 Tool Policy：
`capability=integration_read`、`effect=read_only`、`idempotency=safe`、
`approval=never`。缺少 Policy、Server 被禁用、能力发现失败、Schema Hash 漂移或缺少
必需工具时，Pack 验证都会 Fail Closed。

启用修复时，以精确名称 `ops-actuator` 注册 Actuator：

```text
http://opencitadel-ops-actuator:8091/mcp
```

Actuator 工具只能由修复 Activity 调用；不得加入 Agent Skill 或模型工具目录。

## 启用与验证

1. 确认 Migration、API 与执行内核健康。
2. 部署并限制 Collector。
3. 注册 Collector 并持久化全部只读 Tool Policy。
4. 在运行时设置中确认 `patrol_policy.admission=accepting`。
5. 在部署层保持生产 Fixture Replay 关闭。
6. 创建、验证并激活 Pack。
7. 手动运行并检查结果与证据。
8. 只有手动 Run 成功后才启用 Schedule。
9. 可选：部署/注册 Actuator，并设 `patrol_policy.remediation=enabled`。
10. 完成一次提案、审批、执行与验证闭环。

Pack 变更会生成新版本，必须重新验证和激活。Scheduled Admission、Patrol 执行、修复
审批与复核都生成正式 Run。

## 验证命令

```bash
make test-patrol
make test-actuator
helm lint deploy/helm/opencitadel --values values.production.yaml
helm template opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --values values.production.yaml \
  --set opsCollector.enabled=true \
  --set opsActuator.enabled=true >/dev/null
kubectl kustomize deploy/kustomize/ops-collector >/dev/null
kubectl kustomize deploy/kustomize/ops-actuator >/dev/null
docker compose --env-file .env.example config --quiet
```

部署后确认 Collector 能读 Pod，但不能写 Pod 或读 Secret。Actuator 的 Namespaced Role 对 Namespace 内 Deployment/StatefulSet 授予 Patch，而精确 Workload 白名单由服务校验；分别验证 RBAC 跨 Namespace 拒绝、服务对未注册 Workload 的 `TARGET_DENIED`，以及无 Secret 权限。TCP 探针成功后还必须做认证能力发现与真实目标检查。

破坏性 Fixture Suite 只能通过 `./scripts/run-patrol-fixtures.sh` 运行；脚本管理并删除
自己的临时 kind 集群，除非显式要求保留。

## 证据验证

下载的 Patrol Evidence ZIP 包含冻结 Pack Snapshot、Run、Check、Finding、Report、
Evidence Index、Manifest 与 Chain Signature。先验证 `manifest.json` 中每个 Hash，再按
Manifest 的 Key Id 验证
`HMAC-SHA256(AUDIT_SIGNING_KEY, manifest 原始字节)`。禁止把签名 Key 发送给浏览器或
第三方验证服务。

## 保留与恢复

执行内核 Scheduler 运行有界、带租约的 Retention Tick。产品 Run/Finding 引用可按
`patrol_retention` 过期，Audit Chain 行始终保留。PostgreSQL、对象存储、签名 Key
历史与集成配置必须使用同一恢复点备份。

停止新 Patrol 工作时，设置 `patrol_policy.admission=paused` 并暂停相关 Pack，不要删除证据；
导航与已有 Run 仍保持可读。恢复 Collector 连通性或注册目标后重新验证 Pack，先手动运行，
再设回 `accepting` 并恢复调度。不得把放宽到 Raw Destination 当作恢复捷径。

## 排障

| 现象               | 检查                                                  |
| ------------------ | ----------------------------------------------------- |
| 新 Run 被拒绝      | 全局 `patrol_policy.admission`、Pack 激活状态         |
| Collector 不可用   | MCP URL、DNS、Service、NetworkPolicy、Readiness       |
| 能力不匹配         | 镜像/工具 Schema 是否变化；重新验证 Pack              |
| Target 被拒绝      | Target Ref 与 Namespace/Workload/Endpoint 白名单      |
| 证据不完整         | 必需类型、SHA-256、过期时间、截断                     |
| Run 长期排队/运行  | 执行内核健康、PostgreSQL Claim、模型/Collector 可用性 |
| Scheduled Run 缺失 | Pack 激活、Schedule、Timezone、Scheduler Leader       |
| Retention 停滞     | 执行内核 Scheduler、Leader Lease、保留限制            |
| 修复被拒绝         | 审批结果、冻结参数 Hash、能力基线                     |
| Actuator 失败      | 8091 Policy、Readiness、精确 Workload 白名单          |

日志必须包含 Run、Pack、Session、Check、Request、Target 与 Error Code 标识，但不得包含
凭证或原始 Authorization Header。
