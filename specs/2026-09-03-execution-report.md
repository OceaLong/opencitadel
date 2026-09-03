# 优化计划执行报告（2026-09-03）

四个方向（A/B/C/D）全部执行完毕。改动共 165 个文件，全部 `git add` 暂存、未 commit；完整 patch 备份在会话 scratchpad `final-all.patch`（9767 行）。

## 验证结论（全绿）

- api 单测：1747 passed（0 failed，91 个为 Postgres/Redis 门控跳过）
- Postgres 集成测试（一次性 pgvector 库）：116 passed
- ui：typecheck / eslint / i18n:check / vitest 182 用例 / prettier 全过
- `make lint`、`make quality-check`：通过
- helm：`helm lint` + 13 项 schema 校验矩阵 + 51 文档渲染断言通过
- **验收套件（scripts/run-acceptance-e2e.sh）：27/27 通过、0 跳过、0 flaky**——该套件在 9160014 提交后首次整体跑通
- 本地开发栈：9 容器健康，无头登录实测进入工作台

## 各计划落地情况

- **Plan A（P0）**：7 项全部完成。quickstart 沙箱构建项经核实已存在（审计误报），实际落地为 .env.example 警告注释 + broker 错误信息明确化。
- **Plan B（后端闭环）**：B-1~B-6 完成。仅"API 进程沙箱池显式禁用"（B-6，无实际调用方的潜在陷阱）降级为记录不改。
- **Plan C（前端闭环）**：C-1/C-2/C-3/C-5 完成，C-4 仅做注册链接条件渲染；修改密码/忘记密码（需后端配合）留作 backlog。
- **Plan D（部署与可观测性）**：helm 硬化全部完成（含发现并修复 sandbox 镜像 env 从未被模板引用的问题）；CI helm 参数、release appVersion 守卫、Makefile 测试口径、备份脚本、compose 卫生完成。两项明确裁剪：CI `alembic check` 门禁（autogenerate 有三类误报需先修比较器，已落地 include_object 半步）、compose monitoring profile（Prometheus 配置不支持 env 展开，生产侧由 helm ServiceMonitor 覆盖）。

## 执行中额外发现并修复的问题（计划外）

1. `.env` 的 `SANDBOX_NETWORK` 显式无前缀值导致 broker network not found（第二个"设了就坏"env 地雷）。
2. 登出不清 cookie：`clear_auth_cookies` 删除 `__Host-` cookie 未带 Secure/SameSite，浏览器拒绝过期指令（真实安全 bug）。
3. `QuotaService` 在已开写事务内嵌套开 UoW 触发 `NestedUnitOfWorkError`（A-4 的实现缺陷，被验收测出）——改为复用调用方事务。
4. 合规证据列表被单行坏数据打挂（一个 session 的治理画像 404 导致整个 `/admin/evidence/sessions` 500）——改为跳过并告警。
5. 出网策略拒绝 Docker Desktop 新版的 198.18.0.0/15 容器网段——显式白名单主机现可覆盖该段。
6. 验收套件自身 5 处存量断点（TRUSTED_PROXY/minioadmin/`__Host-` cookie 读取×3/auth 限流桶/向导选择器/采集器鉴权头），逐一修复后套件恢复门禁价值。

## Backlog（未做，建议后续）

- 修改密码 + 忘记密码（前后端配套，Plan C-4.2/4.3）
- CI `alembic check` 门禁（先修 knowledge_base_versions 复合外键与唯一约束的 autogenerate 误报）
- compose 本地 monitoring profile
- 审批过期升级/改派机制（当前超时仍按取消处置，但已有通知）
- 团队审批 fan-out 的 Postgres 集成测试（现为单元级覆盖）
- API 进程沙箱池显式语义化
