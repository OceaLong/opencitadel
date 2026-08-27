# 执行内核 Greenfield 切换证据

本文记录 2026-08-25 完成的破坏性单运行时切换验收证据。目标环境是全新安装；
不提供对旧版本数据库、API、事件、部署或运行时的兼容。

## 最终架构边界

- PostgreSQL Command、Event、Inbox、Outbox、Activity、Timer、Snapshot 和正式投影
  是唯一持久执行协议。
- `api/app/execution_kernel_main.py` 是唯一执行运行时入口。
- Redis 只承担唤醒和通知。Redis 状态丢失不会丢失已接收工作或工作流状态。
- Agent、Ask、知识库摄取、代码库索引、自动化、巡检和修复统一使用 Run Aggregate
  和 Command 路径。
- 审批是正式 Command/状态转换。生产代码中不再保留任何旧执行或人工介入生命周期。

## 破坏性 Schema 契约

仓库只有一个 Alembic head：`0001greenfield_initial`。它创建全新 Schema，不迁移旧数据。
PostgreSQL 初始化安装 `vector`、`uuid-ossp` 和 `pgcrypto`，并隔离三个登录角色：

| 角色 | 职责 |
| --- | --- |
| 应用角色 | 在 RLS 约束下读写产品数据；无执行事件追加或 DDL 权限 |
| 迁移角色 | 只负责 Schema 和扩展迁移 |
| 执行内核角色 | 追加事件并维护正式执行投影；无迁移权限 |

集成测试覆盖全新 PostgreSQL 安装、强制 RLS、跨租户拒绝、Stream Owner 匹配、
事件/审计行不可变、哈希篡改检测、Snapshot 损坏回退和角色授权。

Patrol 与 Remediation 产品记录不能绕过正式接纳：每个持久化 Patrol Run 都有非空
正式 Run 标识。Session turn 通过 PostgreSQL 行锁串行接纳，并由不可变请求 UUID
提供幂等身份；真实双请求并发测试覆盖该约束。执行内核真实进程启动验证覆盖异步
composition root，并以专用登录角色通过 readiness；探针校验已迁移表、追加权限，
以及执行事件不具备修改权限。

## 验证结果

| 门禁 | 结果 |
| --- | --- |
| API 全量测试 | 1,279 passed，5 skipped |
| Sandbox | 32 passed |
| Ops Collector | 33 passed，2 skipped |
| Ops Actuator | 31 passed |
| UI 单元测试 | 36 个文件、133 个测试通过 |
| UI lint / typecheck / 生产构建 | 通过 |
| UI 国际化 | 1,565 个键中英对齐；1,281 个代码引用键均存在 |
| 分层依赖 | 515 个文件 / 1,924 条依赖；5 个契约通过，0 个破坏 |
| 导入豁免 | `ignore_imports` 为零；CI 零豁免契约通过 |
| Python CI 未定义名称门禁 | `ruff check --select F821 app tests` 通过 |
| 新增执行内核代码 | 完整 Ruff 规则通过 |
| 部署 | API/执行内核镜像构建、Compose 配置、Helm lint/template、两套 Kustomize 渲染通过 |
| 文档 | 双语与架构契约检查通过 |
| 补丁完整性 | `git diff --check` 通过 |

UI 国际化工具只提示未使用键清单，不存在缺失或中英不一致。UI 构建提示 Node
第三方 `module.register()` deprecation。API 测试的 5 条警告来自第三方
PyMuPDF/SWIG 的导入期 deprecation；应用自身无 deprecation 警告。

## 负向残留证明

Greenfield 边界契约拒绝旧类名、模块路径、数据表、部署工作负载和环境配置。
仓库扫描中，旧执行词汇只出现在这些负向断言里。正式的 `ActivityWorker`、
`DecisionWorker` 和 `InboxWorker` 是单一执行内核内部组件，不是独立运行时生命周期。

本次切换过程未暂存、未提交任何文件。
