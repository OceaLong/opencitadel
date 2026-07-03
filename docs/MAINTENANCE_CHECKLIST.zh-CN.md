[English](MAINTENANCE_CHECKLIST.md)

# 文档维护检查清单

在变更功能、路由、配置、部署或 UI 文案时使用本清单。

## 何时更新文档

- [ ] 新增或变更 API 路由 → `api/README.md` + `api/README.zh-CN.md`，相关 `docs/architecture/*.md`
- [ ] 新增或变更 UI 路由 → `ui/README.md` + `ui/README.zh-CN.md`，根 README 模块表
- [ ] 新增环境变量 → `.env.example`、`docs/operations/deployment.md`（+ 中文）、`config-source-governance.md`（+ 中文）
- [ ] 新增 `AppConfig` 字段 → `api/config.yaml`、Helm `appConfig`、`config-source-governance.md`（+ 中文）
- [ ] 新增教程或架构文档 → 中英文文件、`docs/README.md`（+ 英文）、根 `README.md`（+ 中文）、文首语言链接
- [ ] Docker 镜像名/数量变更 → `deployment.md`（+ 中文）、Helm README（+ 中文），必要时 `release.yml` 注释

## 双语同步

- [ ] 英文 `topic.md` 与中文 `topic.zh-CN.md` 同步更新
- [ ] 文首链接：`[English](topic.md) · [简体中文](topic.zh-CN.md)`（中文文件可反向）
- [ ] 文内链接：英文链 `*.md`；中文链 `*.zh-CN.md`
- [ ] UI 文案在 `ui/scripts/build-messages.mjs` 中同时添加 `en` 与 `zh` 键

## 准确性核对（人工）

| 领域 | 对照代码 |
|------|----------|
| UI 路由 | `ui/src/app/**/page.tsx` |
| API 路由 | `api/app/interfaces/endpoints/routes.py` 及各路由模块 |
| Compose 镜像 | `docker-compose.yml`、`.github/workflows/release.yml` |
| 沙箱边界 | Chromium 在沙箱；Worker 内 Playwright 经 CDP |
| 集成 UI | 设置弹窗 → 集成 Tab（非 `/settings/integrations`） |
| 对象存储 | `.env.example` 默认；本地用 `COMPOSE_PROFILES=local` + `STORAGE_PROVIDER=minio` |
| 服务 API Key | `X-Api-Key`；入站仅 `/api/a2a` |
| 分享链接 | 默认 TTL 168h；UI 路由 `/share/artifact/[token]` |

## 自动检查

提交文档 PR 前执行：

```bash
./scripts/check-docs.sh
```

CI 在每个 Pull Request 上运行相同脚本。

## 相关

- [文档中心](README.zh-CN.md)
- [贡献指南](../.github/CONTRIBUTING.zh-CN.md)
