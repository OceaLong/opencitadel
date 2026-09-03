# Plan C · 前端体验闭环（Phase 2，预估 3–5 天）

前置：Plan A-5（错误伪装修复）已完成；C-2 依赖后端已有接口，无需等 Plan B；C-3 的通知表单依赖 B-4。

## C-1 全局错误兜底与三态组件

1. 新增 `app/not-found.tsx`、`app/global-error.tsx`（走 next-intl，当前 404 是 Next 内置英文页）。
2. 把 `share-artifact-page-client.tsx:20,63-71` 的 loading/error/content 三态抽成共享组件 `<AsyncBoundary>`（该页是全站闭环典范，直接以它为模板）。
3. 用 `<AsyncBoundary>` 替换两处"加载失败=永久骨架屏"：`app/patrols/[id]/patrol-pack-page-client.tsx:106-113`、`app/patrol-runs/[id]/patrol-run-page-client.tsx:51-58`。
4. 消费已声明未渲染的错误态：`providers/patrol-packs-provider.tsx:145` 的 `error` 在 `app/patrols/page.tsx:24` 与 `components/patrol/patrol-context-panel.tsx:23` 解构并渲染。

**验收**：未知路由显示 i18n 404 页；mock 接口失败时巡检详情页出现可重试的错误态而非骨架屏。

## C-2 "待我审批"收件箱

后端 `GET /api/approvals?status=pending`（分页）已就绪，前端零消费——这是 ops 闭环最关键缺口。

1. 新增 `/approvals` 页面：待办列表（会话名/命令摘要/发起人/剩余 TTL）、批准/拒绝直接调用现有 decide 接口（复用 `approval-actions-bar.tsx` 逻辑）、点击跳转会话详情。
2. 顶栏通知铃铛旁加待审批角标（轮询或挂现有通知 SSE 流）。
3. i18n 双语文案、`usePaginatedList`（Plan A-5 修复后的版本）。

**验收**：另一账号触发审批 → 审批人首页角标出现 → 收件箱内完成决策 → Run 继续执行。

## C-3 巡检模块可用性闭环

1. **编辑模式**：接通零调用方的 `patrolsApi.updatePack`（`lib/api/patrols.ts:46-49`），`PackWizard` 支持 edit 模式预填，详情页（`app/patrols/[id]/patrol-pack-page-client.tsx:196-220`）加"编辑"入口。当前改个 cron 要删库重建。
2. **向导第 3 步去假**：硬编码的"检查项"列表（`components/patrol/pack-wizard.tsx:41-108,305-326`）二选一——要么做成真实可选并提交进 `config`，要么明确标注"模板固定项，仅供预览"（推荐先做后者，改动小、不骗人；真实可配作为后续项记录）。
3. **demo 默认值清理**：`pack-wizard.tsx:116-118` 的 `opencitadel-demo`/`opencitadel-local` 默认值改为空+placeholder；`:154` `namespaces` 支持多值输入（后端 schema 本就是数组）。
4. **向导死路修复**：`:124-128` 的 `listMCPServers()` 加 `.catch` + 空态提示（"未找到采集器，请先在设置→集成中注册"），避免下拉空+下一步禁用+无提示。
5. **通知配置**：待 B-4 落地后，向导"通知"卡片（`:359-362` 现为纯静态文案）接入 `notify_channels` 表单。
6. **修复状态跟随**：`patrol-run-detail.tsx:105-108` 的 remediation 列表跟随 run 的 3s 轮询刷新；`remediation-status.tsx` 补取消/查看详情（后端 `GET /api/patrol-remediations/{id}` 已就绪）。
7. **巡检运行历史 index 页**：`/patrol-runs` 目前 404（只有 `[id]/`），补跨包运行历史列表页，或在 `/patrols` 页加运行历史 tab 并对 `/patrol-runs` 做重定向。

**验收**：建包→编辑 cron→保存生效；采集器接口挂掉时向导给出明确指引；巡检跑完能在历史页看到。

## C-4 账号流程闭环

1. 登录页（`app/login/page.tsx`）与登录弹窗（`login-dialog.tsx:103-106`）的注册链接按"是否开放注册"条件渲染——无邀请机制开放时隐藏，避免链到永久禁用的注册页（`app/register/page.tsx:51-53,71`）。
2. **修改密码**（登录态）：设置弹窗通用面板加"修改密码"，后端补 `POST /api/auth/change-password`（校验旧密码，走现有密码策略）。
3. **忘记密码**（最小闭环，依赖 SMTP 配置）：后端 `POST /api/auth/forgot-password`（发一次性 token 邮件）+ `POST /api/auth/reset-password`；SMTP 未配置时 capability 上报 DISABLED、前端隐藏入口（对齐 A-6 补的 SMTP 变量段）。前后端各半天，若嫌重可只做修改密码、忘记密码记入 backlog。

**验收**：无邀请码场景登录页无死链；改密后旧密码失效；SMTP 配好时忘记密码全流程可走通。

## C-5 导出与系统健康

1. `<a href>` 直连导出改为 `authenticatedFetch` + Blob 下载（`patrolsApi.downloadEvidence`，`lib/api/patrols.ts:80-84` 已是正确写法，照抄）：`app/admin/compliance/report/page.tsx:82-105`、`compliance/page.tsx:147`、审计 CSV 导出。403/500 时走 toast 而不是浏览器跳 JSON 错误页。
2. 管理台加"系统健康"卡片：消费 `GET /api/status`（组件健康，前端连 client 都没有）与 `inferenceApi.getStatus`（`lib/api/inference.ts:61`，现为死代码）——一并把两处后端能力接上 UI；不想做面板就删死代码，二选一（推荐做，改动小）。
3. 会话 shell/文件读取接口（`POST /api/sessions/{id}/shell`、`/file`）暂不做 UI，记入 backlog（VNC 已覆盖主场景）。

**验收**：断网/403 场景导出给出 toast；管理台能看到 pg/redis/推理服务健康状态。

## 执行顺序建议

C-1（基建，先行）→ C-2 → C-3（除第 5 步等 B-4）→ C-5 → C-4。全程保持 `npm run i18n:check`、`npm run api:check` 绿；每项完成后暂存+patch 备份。
