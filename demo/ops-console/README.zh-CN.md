[English](README.md)

# OpsConsole — 演示用内部工单运营后台

面向 **OpenCitadel Web Operator** 演示与 e2e 测试的内部工单与结算后台。日常操作为浏览器表单驱动；同时提供**只读 REST API** 供对账 Skill 使用。

## 功能

- Cookie 登录（`agent` / `agent123`）
- 工单列表（按状态/负责人筛选）
- 结算台账与对账期望值视图
- 普通操作：改状态、指派、添加备注（仅表单 POST）
- 高危操作（独立二次确认页）：
  - **关闭工单** — 输入 `close` 确认
  - **处理退款** — 输入 `refund` 确认

## 只读 REST API

| 方法 | 路径                           | 说明             |
| ---- | ------------------------------ | ---------------- |
| GET  | `/api/tickets`                 | 工单列表（JSON） |
| GET  | `/api/settlements`             | 结算台账         |
| GET  | `/api/reconciliation/expected` | 对账期望值       |
| GET  | `/health`                      | 健康检查         |

业务写操作通过需要登录的 HTML 表单 POST。`POST /api/_seed/reset` 是额外的重置写接口，会重建 SQLite 演示数据；它与上述 JSON 读取接口均不检查登录，也没有环境开关。此服务只用于可信本地演示与测试，不承载生产数据。

## 本地运行

```bash
cd demo/ops-console
pip install -r requirements.txt
python seed.py
uvicorn app:app --host 0.0.0.0 --port 9099
```

访问 http://localhost:9099

## Docker（与 OpenCitadel 一起）

```bash
docker compose --profile local --profile demo up -d --build ops-console
```

Docker 网络内服务名：`ops-console:9099`

默认 Compose 同时加入应用网与沙箱网，并把宿主 `${OPS_CONSOLE_PORT:-9099}` 发布到容器 9099。会话保存在进程内，重启后需要重新登录。SQLite 数据位于容器工作目录，当前 Compose 不挂载持久卷。

## 稳定元素 ID

页面使用稳定的 `id` 属性便于浏览器自动化（`#login-form`、`#btn-confirm-close`、`#btn-confirm-refund` 等）。
