[English](CONTRIBUTING.md)

# 参与 OpenCitadel 贡献

感谢您对 OpenCitadel 的关注与贡献！

## 开发环境

### 前置要求

- Docker 与 Docker Compose
- Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)
- Node.js 22+ 与 npm（UI；与 CI 一致）

### API / 执行内核

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

### 质量门禁

在仓库根目录运行与 CI 一致的只读静态检查：

```bash
make quality-check
./scripts/check-docs.sh
```

`make quality-check` 会执行全仓 Ruff lint/format 检查、Python 架构/进程契约，以及 UI 的
Prettier、生成 API 契约、严格 i18n、TypeScript 与 ESLint。`ui/messages/en.json` 和
`ui/messages/zh.json` 是唯一翻译事实来源，直接同步修改两份文件，不再存在词典生成步骤。

### 全栈（本地）

```bash
cp .env.example .env
# 编辑 .env：设置管理员密码；登录后再配置推理 Credential
docker compose --profile local up --build
```

## Pull Request 流程

1. Fork 仓库，从 `main` 创建功能分支。
2. 保持变更聚焦；每个 PR 只做一类逻辑变更。
3. API 变更请添加或更新测试（`api/tests/`）。
4. 提交前运行 `make quality-check`、API/UI 测试和 UI 生产构建。
5. 若行为或配置有变，请同步更新文档 — 见 [文档维护检查清单](../docs/MAINTENANCE_CHECKLIST.zh-CN.md)，并运行 `./scripts/check-docs.sh`。
6. 提交信息清晰（推荐 [Conventional Commits](https://www.conventionalcommits.org/)）。

## 代码风格

- **Python**：Ruff 策略只由根目录 `ruff.toml` 定义，不接受 baseline 或逐文件债务豁免。
- **TypeScript/React**：使用现有 Prettier 与 ESLint 配置，并保持既有 Server/Client 边界。
- **UI 文案**：统一使用 next-intl 词典；严格检查会拒绝缺失、未使用、未登记动态调用和面向用户的硬编码文本。

## 新手友好 Issue

可查找标有 `good first issue` 的 Issue——文档、测试与 MCP 服务器模板都是很好的入门点。

## 许可证

提交贡献即表示您同意将贡献内容以 [Apache License 2.0](LICENSE) 授权。
