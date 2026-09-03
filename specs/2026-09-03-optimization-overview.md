# OpenCitadel 功能闭环优化 · 总体 Spec

- 日期：2026-09-03
- 基线：HEAD = `9160014`（全功能旧版；v2 重构在 dangling commit `7569a86`，不在本计划范围内）
- 来源：三路全仓审计（API / UI / 基础设施）+ 运行态核查
- 约定：本目录文档只留本地，不 git commit；所有代码改动只 `git add` 暂存、不自动 commit；有价值的工作区改动先 `git diff > patch` 备份到仓库外

## 1. 背景与审计结论摘要

代码整体成色良好：审计/合规、定时调度（自研 asyncio + Redis 租约选主，跑在 execution-kernel 容器）、知识库、会话执行链路均为真闭环；前端零 TODO/零 mock、i18n 全对齐、前后端契约由 OpenAPI 生成强制校验；CI 含 Trivy/CodeQL/gitleaks/验收测试。

问题集中在四类：

1. **运行态断链**：`opencitadel-sandbox` 镜像挂在 `profiles: [fixed-sandbox]`，quickstart 不构建 → broker 建沙箱 500 循环（2026-09-03 已本地构建修复，交付面修复见 Plan A）。
2. **半成品功能**：A2A 任务生命周期、配额绕过、团队审批静默、回收站清理、巡检包编辑、待审批收件箱、错误伪装空态等约 15 处。
3. **"配置了但不生效"陷阱**：审批 TTL 旋钮、RLS 签名密钥双源、多个死配置项。
4. **部署漂移**：helm 与 compose 两条路径在密钥、TLS、探针、镜像名上不一致；CI 与本地迁移环境不一致（pg_trgm）。

完整逐条清单见各 Plan 文档。

## 2. 目标与非目标

**目标**
- 把"用户够得着但走不通"的链路全部闭环或明确标注降级。
- 消灭"看起来配置了、实际不生效"的控制面旋钮（这是最危险的一类）。
- helm / compose / CI 三条交付路径与代码实际要求对齐，全新环境一次起成。
- 错误永不伪装成空数据（合规/审计域视为安全语义要求）。

**非目标**
- 不恢复/合并 v2（`7569a86`）架构。
- 不新增大型功能域；C 方向的"忘记密码"限最小闭环。
- 不引入 celery/apscheduler 等新调度设施；沿用现有 kernel leader tick。

## 3. 分期 Roadmap

| 阶段 | 内容 | 计划文档 | 预估 | 前置 |
|---|---|---|---|---|
| Phase 0 | P0 修复包（运行态断链交付面、CI 必挂、密钥陷阱、配额、前端错误伪装） | plan-a-p0-fixes.md | 1–2 天 | 无 |
| Phase 1 | 后端功能闭环（A2A、审批、回收站、Patrol 通知、搜索） | plan-b-backend-closure.md | 3–5 天 | Phase 0 |
| Phase 2 | 前端体验闭环（巡检编辑、审批收件箱、错误兜底、账号流程） | plan-c-frontend-closure.md | 3–5 天 | Phase 0（可与 Phase 1 并行） |
| Phase 3 | 部署一致性与可观测性（helm 漂移、schema 左移、metrics、备份、迁移门禁） | plan-d-deploy-observability.md | 3–5 天 | Phase 0 |

依赖说明：Phase 1 与 Phase 2 有两处接口耦合——审批收件箱（C-2 消费 B-2 的通知修复）、A2A 无 UI 依赖；其余可并行。

## 4. 统一验收方式

- 单测/契约测试：`make test-api`（补 `OPENCITADEL_REQUIRE_POSTGRES_TESTS=1` 严格模式，见 Plan A）、`make test-ui`。
- 验收栈：`scripts/acceptance/runner.py`（需先落地 Plan A 的 `TRUSTED_PROXY_CIDRS` 修复，否则起不来）。
- 冒烟：从零 `docker volume` 起栈（验证 pg_trgm/初始化路径）、headless 登录探针（scratchpad/login-probe.js 模式）。
- 每阶段完成标准：该 Plan 的验收清单全绿 + 改动全部处于 `git add` 暂存态 + patch 备份到仓库外。

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| RLS 签名密钥统一涉及已有库中一次性落库的 HMAC（`execution_authorization_secrets` 单行表，无更新路径） | Plan A 采用"取值来源统一 + 可重入轮换工具"两步走；先在全新库上验证，再对存量库跑轮换 |
| 工作区未提交改动可能被并发操作清掉（历史上发生过两次） | 每完成一个条目立即 patch 备份到 scratchpad；长命令后重新核实 git/容器状态 |
| 本机 Docker Desktop 长构建易挂 | 构建日志落盘 + 构建前后确认 daemon 存活 |
| 配额下沉到 service 层可能改变 A2A/定时任务既有行为（原本无限制） | 提供 per-source 豁免开关，默认收紧但可配置回退 |
| `.env.example` 默认值调整影响老用户的升级习惯 | 在 README 变更说明中列 breaking 差异；quickstart 保持自动改写逻辑 |

## 6. 已知但明确不修（记录在案）

- PDF 报告 501 降级：容器内依赖已装、capability 探针如实上报，属设计内降级。
- 团队软删除只支持 transfer/cascade：`team_service.py:39` 明示为刻意设计。
- `/sessions` 列表页重定向到 `/`：非缺陷。
