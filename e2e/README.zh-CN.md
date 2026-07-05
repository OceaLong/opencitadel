[English](README.md) · [简体中文](README.zh-CN.md)

# OpenCitadel E2E 测试

基于 Playwright 的端到端冒烟测试，覆盖 OpenCitadel UI 与 **Web Operator 演示后端**（OpsConsole）。

## 覆盖范围

| 套件 | 文件 | 说明 |
|------|------|------|
| OpsConsole 演示 | `web-operator.spec.ts` | 登录页、登录后工单列表 |
| 平台冒烟 | `web-operator.spec.ts` | OpenCitadel 首页可加载 |

测试配合 [教程 4：受治理 Web Operator](../docs/tutorials/04-governed-web-operator.zh-CN.md) — 在演示栈启动后运行。

## 前置条件

- Node.js >= 22
- 已运行的 OpenCitadel 栈（默认 `http://localhost:8088`）
- OpsConsole 测试需启用 demo profile

```bash
# 在仓库根目录 — 启动平台 + OpsConsole 演示
docker compose --profile local --profile demo up -d --build
docker compose build opencitadel-sandbox   # 若尚未构建
```

OpsConsole 默认地址：`http://localhost:9099`（可通过 `OPS_CONSOLE_URL` 覆盖）。

## 安装与运行

```bash
cd e2e
npm install
npm test
```

环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PLAYWRIGHT_BASE_URL` | `http://localhost:8088` | OpenCitadel UI 基址 |
| `OPS_CONSOLE_URL` | `http://localhost:9099` | OpsConsole 演示后端 |

有界面模式（调试）：

```bash
npm run test:headed
```

## 相关文档

- [受治理 Web Operator 教程](../docs/tutorials/04-governed-web-operator.zh-CN.md)
- [Web Operator 架构](../docs/architecture/web-operator.zh-CN.md)
- [OpsConsole 演示 README](../demo/ops-console/README.zh-CN.md)
