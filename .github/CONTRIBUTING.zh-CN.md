[English](CONTRIBUTING.md)

# 参与 OpenCitadel 贡献

感谢您对 OpenCitadel 的关注与贡献！

## 开发环境

### 前置要求

- Docker 与 Docker Compose
- Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)
- Node.js 22+ 与 npm（UI；与 CI 一致）

### API / Worker

```bash
cd api
uv sync
uv run pytest
```

### UI

```bash
cd ui
npm install
npm run test
npm run build
```

### 全栈（本地）

```bash
cp .env.example .env
# 编辑 .env：设置 LLM API Key 与管理员密码
docker compose --profile local up --build
```

## Pull Request 流程

1. Fork 仓库，从 `main` 创建功能分支。
2. 保持变更聚焦；每个 PR 只做一类逻辑变更。
3. API 变更请添加或更新测试（`api/tests/`）。
4. 提交前运行 `uv run pytest` 与 `npm run test`。
5. 若行为或配置有变，请同步更新文档 — 见 [文档维护检查清单](../docs/MAINTENANCE_CHECKLIST.zh-CN.md)，并运行 `./scripts/check-docs.sh`。
6. 提交信息清晰（推荐 [Conventional Commits](https://www.conventionalcommits.org/)）。

## 代码风格

- **Python**：遵循现有模式；鼓励类型注解；可在编辑器中运行已配置的格式化工具。
- **TypeScript/React**：遵循现有组件结构；沿用 `ui/` 中已有的 Server/Client 组件边界。

## 新手友好 Issue

可查找标有 `good first issue` 的 Issue——文档、测试与 MCP 服务器模板都是很好的入门点。

## 许可证

提交贡献即表示您同意将贡献内容以 [Apache License 2.0](LICENSE) 授权。
