# 执行内核 Greenfield 切换证据

[English](execution-kernel-cutover-evidence.md)

本文定义破坏性单运行时切换的可复现证据契约。OpenCitadel 面向全新安装，不提供对
前代构建的数据库、API、事件、部署或运行时兼容。

## 最终架构边界

- PostgreSQL Command、Event、Inbox、Outbox、Activity、Timer、Snapshot 与正式投影
  是唯一持久执行协议。
- `api/app/execution_kernel_main.py` 是唯一执行运行时入口。
- Redis 只承担唤醒与通知；Redis 状态丢失不会丢失已接收工作或工作流状态。
- Agent、Ask、知识库摄取、代码库索引、自动化、Patrol 与修复统一使用 Run Aggregate
  和 Command 路径。
- 审批是正式 Command/状态转换；生产代码不保留前代执行或人工介入生命周期。

## 破坏性 Schema 契约

仓库只有一个 Alembic Head：`0001greenfield_initial`。它创建全新 Schema，不升级前代
数据。PostgreSQL 初始化安装 `vector`、`uuid-ossp` 与 `pgcrypto`，并分离 Migration、
Application 和 Execution Kernel 登录角色。

严格数据库套件覆盖全新 PostgreSQL 安装、强制 RLS、跨租户拒绝、Stream Owner 匹配、
事件/审计行不可变、哈希篡改检测、Snapshot 损坏回退和角色授权。Patrol 与 Remediation
记录不能绕过正式接纳；Session Turn 通过 PostgreSQL 行锁串行，并以不可变 Request UUID
提供幂等身份。

## 验证权威来源

当前状态只来自可执行门禁，不维护会漂移的通过/跳过数量：

| 边界 | 权威证据 |
| --- | --- |
| 必需产品旅程 | [`contracts/acceptance-evidence.schema.json`](../../contracts/acceptance-evidence.schema.json) 与 zero-skip Reporter |
| 全栈结果 | 本地 `tmp/acceptance/<run-id>/manifest.json`；必跑 [`acceptance-e2e` CI Job](../../.github/workflows/ci.yml) 的 `acceptance-evidence` Artifact |
| API、数据库、RLS、架构 | `OPENCITADEL_REQUIRE_POSTGRES_TESTS=1 uv run pytest -q` 与 `uv run lint-imports` |
| UI | CI 中的 Format、i18n、生成 API 契约、Typecheck、Lint、单元套件与生产构建 |
| 部署/发布 | Compose Render、Helm Lint/Template、Kustomize Render、Release Matrix 契约与镜像构建 |
| 文档 | `./scripts/check-docs.sh` |

验收 Manifest 具有内容哈希，记录 Requirement Coverage、生产/验收镜像 Digest、Alembic
Head、服务健康与重启状态、Sandbox 生命周期、Artifact 及归属资源残留。完整运行只有在
每个必需 ID 都对应成功 Playwright 测试、无必需测试跳过、Manifest 校验通过且清理达到
声明的残留契约时才成功。

确定性推理 Provider 是 Compose `acceptance` Profile 下的测试 Fixture。Release 契约禁止
它进入 Helm、Kustomize、Quickstart、生产配置与七镜像 Release Matrix。外部 Provider
Canary 只是可选兼容性信号，不属于切换证据。

## 复现

```bash
./scripts/run-acceptance-e2e.sh --disposable
```

Runner 创建唯一 Compose Project 与 Run Namespace，只通过公共产品路径完成设置，在成功
和失败时都捕获证据，并且只删除 Project/Run Label 精确匹配的资源。保留 Volume 行为与
清理诊断见 [E2E 验收指南](../../e2e/README.zh-CN.md)。

## 负向残留证明

仓库契约拒绝前代类名、模块路径、数据表、部署 Workload 与环境设置。正式
`ActivityWorker`、`DecisionWorker` 和 `InboxWorker` 是单一执行内核内部组件，不是独立
运行时生命周期。

切换流程不暂存、不提交文件。
