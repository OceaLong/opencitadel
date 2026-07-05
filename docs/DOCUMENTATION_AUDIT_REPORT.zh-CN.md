[English](DOCUMENTATION_AUDIT_REPORT.md)

# 文档审计报告

**状态：** 历史快照 — 不代表当前文档实时状态。最新清单与过期风险见 [文档清单](DOCUMENTATION_INVENTORY.zh-CN.md)；PR 流程见 [维护清单](MAINTENANCE_CHECKLIST.zh-CN.md)。

**日期：** 2026-07-06  
**代码基线：** 当前工作区（含未提交的 ingestion/OCR/上传相关改动）  
**范围：** 全部 Markdown、模块 README、双语配对、架构图、与实现的一致性

## 摘要

仓库已有较成熟的文档体系（约 80 个 Markdown、中英文配对、`DOCUMENTATION_INVENTORY`、`check-docs.sh`）。本次审计将文档与近期 codebase/知识库摄取实现同步，并补齐结构缺口（nginx 模块 README、KB 摄取架构文档、上传限制矩阵）。

**结果：** 文档检查通过（`./scripts/check-docs.sh`）。

## 新增文档

| 文档 | 用途 |
|------|------|
| [architecture/knowledge-base-ingestion.zh-CN.md](architecture/knowledge-base-ingestion.zh-CN.md)（+ 英文） | KB 解析、OCR `vision_llm`、GraphRAG、向量降级、不可恢复失败、对账 |
| [nginx/README.zh-CN.md](../nginx/README.zh-CN.md)（+ 英文） | 网关路由、SSE/WS、动态 DNS、上传上限 |

## 更新文档

| 区域 | 变更 |
|------|------|
| [DOCUMENTATION_INVENTORY.zh-CN.md](DOCUMENTATION_INVENTORY.zh-CN.md)（+ 英文） | 修正 `technical-decisions` 图示字段；新增 KB 摄取与 nginx 行 |
| [architecture/overview.zh-CN.md](architecture/overview.zh-CN.md)（+ 英文） | 摄取任务类型表；Worker KB 对账职责 |
| [architecture/events.zh-CN.md](architecture/events.zh-CN.md)（+ 英文） | 摄取 `step` id；`DOCUMENT_PARSE_FAILED` |
| [architecture/task-recovery.zh-CN.md](architecture/task-recovery.zh-CN.md)（+ 英文） | Agent 与 KB 摄取恢复边界 |
| [architecture/config-source-governance.zh-CN.md](architecture/config-source-governance.zh-CN.md)（+ 英文） | 存储 Provider 矩阵；跨层上传限制 |
| [architecture/codebase-reindex.zh-CN.md](architecture/codebase-reindex.zh-CN.md)（+ 英文） | 200 MB ZIP 限制；`sandbox_result.py` |
| [operations/deployment.zh-CN.md](operations/deployment.zh-CN.md)（+ 英文） | 上传大小限制章节 |
| [tutorials/02-internal-knowledge-base.zh-CN.md](tutorials/02-internal-knowledge-base.zh-CN.md)（+ 英文） | OCR、50 MB 文档限制、架构文档链接 |
| [docs/README.zh-CN.md](README.zh-CN.md)（+ 英文） | 新文档索引 |
| [ui/README.zh-CN.md](../ui/README.zh-CN.md)（+ 英文） | `constants.ts`、`file.ts` |
| [MAINTENANCE_CHECKLIST.zh-CN.md](MAINTENANCE_CHECKLIST.zh-CN.md)（+ 英文） | 摄取与上传限制维护规则 |
| [scripts/check-docs.sh](../scripts/check-docs.sh) | nginx 配对、KB 摄取索引、inventory 图示防护 |

## 删除文档

**无。** 未删除任何 Markdown 文件。此前已废弃的*内容*（侧栏 Knowledge、UI `/admin/usage`、Settings 语言切换）已在代码与文档中修复；inventory 现将其标为回归检查项。

## 架构与图示覆盖

| 主题 | 状态 |
|------|------|
| 系统拓扑 | [overview.zh-CN.md](architecture/overview.zh-CN.md) — 8+ Mermaid 图 |
| 技术选型与对比 | [technical-decisions.zh-CN.md](architecture/technical-decisions.zh-CN.md) |
| KB 摄取流水线 | **新增** [knowledge-base-ingestion.zh-CN.md](architecture/knowledge-base-ingestion.zh-CN.md) |
| Codebase 摄取 | [codebase-reindex.zh-CN.md](architecture/codebase-reindex.zh-CN.md) |
| 网关 | **新增** [nginx/README.zh-CN.md](../nginx/README.zh-CN.md) |
| 思维导图 / draw.io | **无** — 均为 Markdown 内嵌 Mermaid |

## 双语覆盖

- 正式文档：**配对**（`*.md` + `*.zh-CN.md`）
- UI 文案：`ui/messages/en.json` / `zh.json`（运行时 locale `zh`，文档文件名 `zh-CN`）
- 配置注释：混合（`.env.example` 双语行；`config.yaml` / nginx 中文注释）

## 剩余缺口（建议人工跟进）

| 项 | 优先级 | 说明 |
|----|--------|------|
| API 路由表漂移 | 中 | `api/README.md` 体量大，无与 `routes.py` 自动同步 |
| 仓库内 OpenAPI | 低 | 仅运行时 `/docs` |
| CHANGELOG | 低 | 无项目级 CHANGELOG |
| 前端 E2E 文档 | 低 | 前端测试覆盖薄，README 勿过度声明 |
| `ocr_llm_resolver.py` | N/A | 计划中提及但工作区无此文件；文档描述 Worker 内联解析 |

## 验证

```bash
./scripts/check-docs.sh   # 2026-07-06 通过
```

## 相关

- [文档清单](DOCUMENTATION_INVENTORY.zh-CN.md)
- [维护检查清单](MAINTENANCE_CHECKLIST.zh-CN.md)
