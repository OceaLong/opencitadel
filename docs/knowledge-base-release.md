# 知识库功能发布与回滚

## 批次 A（P0 安全与一致性）
- 索引原子替换（`replace_index_chunks`）
- reindex 互斥/幂等
- SSRF URL 校验
- 文档读取 kb 归属校验
- 前端会话切换竞态修复

**验证**
- 上传文档并成功索引
- 索引过程中重复点击「重新索引」不触发第二个任务
- 内网 URL 添加被拒绝
- 快速切换知识库不会串会话

## 批次 B（P1 质量）
- GraphRAG 按文档配额
- OCR 扫描 PDF 回退
- KB 向量开关独立于 memory.vector_enabled
- 文档/知识库失败状态展示

## 批次 C（P2 保障）
- 后端/前端关键测试
- 检索/索引结构化日志

## 回滚
- 关闭 `knowledge_base.graphrag.enabled`
- 关闭 `knowledge_base.rerank.enabled`
- 设置 `knowledge_base.ocr.mode: off`
- 关闭 `knowledge_base.vector_enabled` 降级为 BM25
