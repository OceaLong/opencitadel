[English](MAINTENANCE_CHECKLIST.md)

# 文档维护检查清单

在变更功能、路由、配置、部署或 UI 文案时使用本清单。

**相关治理文档**

| 文档 | 职责 |
|------|------|
| [文档清单](DOCUMENTATION_INVENTORY.zh-CN.md) | 全部文档的权威列表、权威级别、过期风险 |
| 本清单 | 贡献者可执行的 PR 步骤 |

## 何时更新文档

- [ ] 新增或变更 API 路由 → `api/README.md` + `api/README.zh-CN.md`，相关 `docs/architecture/*.md`
- [ ] 新增或变更 LLM 端点/模型行为 → `llm-endpoints-and-models.md`（+ 英文）、`deployment.md`（+ 中文）、`ui/README.md`（+ 中文）
- [ ] 新增或变更 UI 路由或 HITL 组件 → `ui/README.md` + `ui/README.zh-CN.md`、`frontend-ui.md`（+ 中文）、根 README 模块表
- [ ] 新增环境变量 → `.env.example`、`docs/operations/deployment.md`（+ 中文）、`config-source-governance.md`（+ 中文）
- [ ] 新增 `AppConfig` 字段 → `api/config.yaml`、Helm `appConfig`、`config-source-governance.md`（+ 中文）
- [ ] 新增教程或架构文档 → 中英文文件、`docs/README.md`（+ 英文）、根 `README.md`（+ 中文）、文首语言链接
- [ ] KB/Codebase 摄取变更 → `knowledge-base-ingestion.md`（+ 中文）、`codebase-reindex.md`（+ 中文）、教程 02（+ 中文）、`events.md`（+ 中文）
- [ ] 上传限制变更 → `nginx/README.md`（+ 中文）、`ui/src/lib/constants.ts`、`config-source-governance.md`（+ 中文）、`deployment.md`（+ 中文）
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
| LLM 端点/模型 | `llm_endpoint_routes.py`、`models-settings.tsx`、设置两步流程 |
| 任务恢复 | `recoverable_task_retry.py`、`task-recovery.md`（+ 中文） |
| Compose 镜像 | `docker-compose.yml`、`.github/workflows/release.yml` |
| 沙箱边界 | Chromium 在沙箱；Worker 内 Playwright 经 CDP |
| 集成 UI | 设置弹窗 → 集成 Tab（非 `/settings/integrations`） |
| 对象存储 | `.env.example` 默认；quickstart 首次运行设置 `COMPOSE_PROFILES=local` + `STORAGE_PROVIDER=minio` |
| 上传限制 | `nginx/nginx.conf`、`ui/src/lib/constants.ts`、AppConfig `knowledge_base.document.max_bytes`、`server.marketplace_max_upload_bytes` |
| KB 摄取 / OCR | `knowledge_base/ingestion_runner.py`、`worker/main.py`、`knowledge-base-ingestion.md`（+ 中文） |
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
- [文档清单](DOCUMENTATION_INVENTORY.zh-CN.md)
- [贡献指南](../.github/CONTRIBUTING.zh-CN.md)
