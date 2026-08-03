[English](README.md)

# Ops Patrol 一次性故障实验室

本实验室只接受 `kind-opencitadel-patrol-*` context，且目标 namespace 必须带有 `opencitadel.io/disposable-patrol-demo=true` 标签。context 为空、疑似生产环境或未知集群时，脚本会立即拒绝执行。

在仓库根目录运行 `./scripts/run-patrol-fixtures.sh`。脚本会创建一次性 kind 集群、逐一应用并重置 20 个案例、每次重置后校验真实基线签名、验证 Collector ServiceAccount 无写权限、通过真实 Collector 适配器观测十个 Kubernetes/日志案例，并执行服务端权威的 20 案例回放。实测结果写入 `tmp/patrol-fixture-score.json`，没有任何分数字段被硬编码为通过。仅在本地排障时设置 `PATROL_KEEP_DEMO_CLUSTER=true`。

Fixture 会创建故障工作负载和合成 Warning Event，严禁用于共享或生产集群。

前置工具包括 Docker、kind、kubectl、jq、uv，并需为固定版本 kind Node 与 Fixture 镜像预留足够本地资源。脚本会预载运行镜像，将机器可读评分写入 `tmp/`，并在成功或失败后删除集群；仅显式 Keep Flag 会改变清理行为。

Release 门禁要求见 [Ops Patrol 运维手册](../../docs/operations/ops-patrol.zh-CN.md#验证)。
