# 受治理 Web Operator（端到端教程）

本教程使用内置 **OpsConsole** 演示后台，走通**受治理的企业内网 Web Operator** 场景。

## 前置条件

- Docker Compose
- 在 OpenCitadel 设置中配置 LLM API Key
- 启用 Web Operator Skill

## 1. 启动平台 + 演示后台

```bash
cp .env.example .env
# 设置 BOOTSTRAP_ADMIN_PASSWORD 与 LLM Key

docker compose --profile local --profile demo up --build
```

- OpenCitadel UI：http://localhost:8088
- OpsConsole（工单运营后台）：http://localhost:9099（Docker 网络内：`ops-console:9099`）

演示账号：`agent` / `agent123`

## 2. 创建 Web Operator 会话

1. 首页选择 **Web Operator** Skill。
2. 发送任务，例如：*登录 OpsConsole，打开工单 #2，将状态改为 in_progress，然后走退款确认流程。*
3. 在**归属声明对话框**中选择：
   - **企业自有/自建**
   - 域名白名单：`ops-console`（宿主机测试可用 `localhost`）
   - 门控档位：**标准**（计划 + 首访域名 + 高危操作）

## 3. 观察治理行为

| 步骤 | 预期门控 |
|------|----------|
| 计划 | 一次性计划审批 |
| 首次导航到 OpsConsole | 域名审批（白名单内免审） |
| 改状态/指派 | 标准档下免逐次审批 |
| 退款/关单 | 逐工具审批 |
| 卡住 | VNC 人工接管 |
| 操作失误 | 检查点回滚（Docker 含浏览器 Profile） |
| 接管超时（30 分钟） | 会话暂停 → **待人工** |

## 4. 审计产物

会话结束后下载：

- `audit-report.md` — 人类可读摘要
- `audit-report.json` — 结构化导出（治理动作 + 脱敏工具调用）

也可在 `/admin/audit` 检索导出。

## 5. 排期自动化

1. 打开 **自动化** → 新建任务，选择 Web Operator Skill。
2. 配置 `operator_scope`、域名、门控档位、可选 MCP 通知渠道。
3. 使用 interval/cron/webhook 触发。

## 6. E2E 测试

```bash
cd e2e && npm install && npx playwright test
```

demo profile 运行时设置 `OPS_CONSOLE_URL=http://localhost:9099`。

详见：[Web Operator 架构](../architecture/web-operator.zh-CN.md)
