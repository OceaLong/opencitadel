[English](README.md)

# Ops Patrol 一次性故障实验室

本实验室只接受 `kind-opencitadel-patrol-*` context，且目标 namespace 必须带有 `opencitadel.io/disposable-patrol-demo=true` 标签。context 为空、疑似生产环境或未知集群时，脚本会立即拒绝执行。

在仓库根目录运行 `./scripts/run-patrol-fixtures.sh`。脚本会创建一次性 kind 集群、逐一应用并重置 20 个案例、每次重置后校验真实基线签名、验证 Collector ServiceAccount 无写权限、通过真实 Collector 适配器观测十个 Kubernetes/日志案例，并执行服务端权威的 20 案例回放。实测结果写入 `tmp/patrol-fixture-score.json`，没有任何分数字段被硬编码为通过。仅在本地排障时设置 `PATROL_KEEP_DEMO_CLUSTER=true`。

Fixture 会创建故障工作负载和合成 Warning Event，严禁用于共享或生产集群。

CI 还会设置 `PATROL_RUN_REMEDIATION_FIXTURE=true` 执行第 21 个案例：观测故障、通过已认证的执行器重启、验证幂等重放、恢复健康工作负载并复检恢复结果。运行器会生成临时执行器 Token 并传给 MCP 客户端。演练 RBAC 向实际执行器 ServiceAccount 授予演练命名空间的权限；基础部署在 `opencitadel` 中的原有权限仍然保留。

所有清单在回放前都要通过服务端严格 Schema 校验。运行失败时，脚本会先将 kind 日志导出到 `tmp/patrol-fixture-logs/`，再删除自己创建的集群；CI 会上传这些日志及执行器构建日志以便排障。

前置工具包括 Docker、kind、kubectl、jq、uv，并需为固定版本 kind Node 与 Fixture 镜像预留足够本地资源。脚本会预载运行镜像，将机器可读评分写入 `tmp/`，并在成功或失败后删除集群；仅显式 Keep Flag 会改变清理行为。

Release 门禁要求见 [Ops Patrol 运维手册](../../docs/operations/ops-patrol.zh-CN.md#验证)。

此实验验证 Collector/Actuator Adapter、固定 Fixture 判定和权限边界；Patrol 第 21 个 Fixture 是修复案例，与全栈 AC21 容量门禁编号不同，不能证明完整产品审批链或容量验收。全 AC21 容量验收目前仍未完成。默认 Runner 创建并清理自己的集群；显式传入 `PATROL_DEMO_CONTEXT` 时使用已存在的一次性 Context，不会删除外部创建的集群，且需自行准备 Fixture 镜像与 Python 依赖。
