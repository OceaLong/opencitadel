# OpsConsole — 演示用内部工单运营后台

纯表单、无 REST API 的内部工单运营后台，供 **OpenCitadel Web Operator** 演示与 e2e 测试使用，仅支持浏览器自动化操作。

## 功能

- Cookie 登录（`agent` / `agent123`）
- 工单列表（按状态/负责人筛选）
- 普通操作：改状态、指派、添加备注
- 高危操作（独立二次确认页）：
  - **关闭工单** — 输入 `close` 确认
  - **处理退款** — 输入 `refund` 确认

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
docker compose --profile local --profile demo up ops-console
```

Docker 网络内服务名：`ops-console:9099`

## 稳定元素 ID

页面使用稳定的 `id` 属性便于浏览器自动化（`#login-form`、`#btn-confirm-close`、`#btn-confirm-refund` 等）。
