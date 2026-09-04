# Plan K3 · 可插拔扩展面统一

遵守总体 spec 决策 D8/D9/D10/D11。范围：`api/app/domain/services/tools/`、`skills/`、`api/app/application/execution/decisions/`、`activities/`、`activity_registry.py`、`agent_tool_catalog.py`、`tool_catalog.py`、`infrastructure/external/tools/`、`search/providers.py`、`capability_service.py`、`skill_service.py`、`composition/kernel.py`。依赖 K1 的 decision_data 形状；与 K2 并行。

## K3-1 工具执行契约 v2（P1-8，D8）

- 新增 `domain/services/tools/errors.py`：`ToolInvocationError(message, kind)`，kind ∈ {invalid_arguments, capability_denied, not_found, execution_failed}。
- `BaseTool`：`_filter_parameters`（base.py:83-86）升级为按 `@tool` 声明的 JSON schema 做必填/类型前置校验，失败抛 `ToolInvocationError(invalid_arguments)`；各原生工具内面向调用方的 `raise ValueError` 改抛 `ToolInvocationError`（knowledge_base_tools.py:62,253 等，全量 grep 清扫）。
- `activities/tool_call.py:52`：`invoke` 外设异常边界——`ToolInvocationError`/`CapabilityDeniedError` → `ToolResult(success=False, error=归一化消息)`，作为成功的 activity outcome 返回（tool 消息进模型循环，模型可纠错重试）；仅基础设施异常（连接/超时/取消）继续按 activity 失败处理。
- `decisions/agent.py`：确认失败 tool result 走"继续对话"路径而非 FailRun（现有 agent 循环消费 outcome 的分支核对补齐）。
- 测试：模型传缺参/多参/坏类型三例 → Run 继续且 tool 消息含错误；沙箱不可达 → activity 失败按原语义。

## K3-2 目录快照一致性（P1-9，D9）

- `agent_tool_catalog.py`：`_build()` 结果缓存为 `CatalogSnapshot`（工具定义列表 + policy 摘要 + skill 指纹 + MCP/A2A 服务器指纹）；`definitions()` 返回快照并由 model.call handler 落入 decision_data（`catalog_snapshot` 字段，K1 的 outcome 表承载）；`invoke()` 接受快照参数：按快照校验工具存在性与 policy，目录漂移（工具消失/skill 被禁）→ `ToolInvocationError(not_found)` 喂回模型，不再 raise 击穿。
- 同一 Run 步内 `definitions`/`retrieve`/`invoke` 共享一次 `_build`（内存缓存 per handler 调用），消除每 tool call O(全目录) 重建；MCP/A2A initialize 仅在快照指纹变化时重连。
- `retrieve()` 的 `"kb_search"` 硬编码（:148-149）改为按 ToolSpec 的 `retrieval=True` 标记选取。

## K3-3 声明式注册收敛（P1-10、P2-8，D10）

- 新增 `application/execution/activity_types.py`：全部 activity 类型字符串常量单源；handler 类属性与决策侧（decisions/agent.py:47,70,133、automation.py:15、resource_build.py:16、patrol.py:16-19）统一引用；`decisions/__init__.py:29-51` 的 if/elif 链改 `dict[RunFamily, DecisionPlannerSpec]` 注册表，未注册 family 启动期报错。
- 内核启动自检（composition/kernel.py）：断言各 planner 声明的 `emits_activity_types ⊆ activity_registry.registered_types`（DecisionPlannerSpec 增加 emits 声明）。
- 工具装配单点：新增 `ToolSpec` 声明式注册表（name、factory、policy、modes、retrieval 标记、依赖声明），`agent_tool_catalog._build` 由 spec 表驱动；**删除 `tool_registry.py` 的 `build_default_tools`/`build_ask_tools`**（唯一引用的 test_search_providers.py:137 改用 ToolSpec 构造）；Vision 两工具（VisionTool/VisionGroundingTool）以 ToolSpec 正式接入生产目录（AGENT mode，READ_ONLY policy）。
- 死代码删除：`tools/patrol.py`（PatrolTool 零引用）、`MCPTool.register_schema`（mcp.py:78-95）、`CapabilityPolicy.for_child`（capability_policy.py:125-146）；`activities/__init__.py` 导出补齐或删掉误导性 re-export（组装点是唯一真源，__init__ 只留注释指向）。

## K3-4 MCP/A2A 信任边界（P1-11，D11）

- 抽出 browser 的 UNTRUSTED 包裹为共享工具 `infrastructure/security/untrusted_content.py`；`mcp_client.py:423-431` 工具 description 截断（512 字符）+ 包裹后进 function schema，inputSchema 限制深度（≤8）与序列化尺寸（≤16KB），超限拒绝该工具并告警；`:497` structuredContent 与 A2A 返回（a2a_client.py:128-138 Agent Card 字段含）统一过包裹器。
- `connection_pool.py`（P2-9）：acquire 锁降到 fingerprint 级（启用已存在未用的 `_PoolEntry.lock`）；调用连续失败（≥3）invalidate 条目强制重建；docstring 与实现对齐。

## K3-5 Skill 语义修缮（P1-12、P2-10）

- `allowed_tools` 语义显式化：模型层 `list[str] | None`，None=不限制、[]=禁全部；`skill_import.py` frontmatter 改用 `yaml.safe_load` 解析，支持列表；导入路径无声明时落 None 并在 UI 提示"未限制工具"；`capability_policy.py:90` 分支按新语义。
- a2a 组标识：`tool_names.py` 导出常量与展开函数，`skill_service.py:188-192` 复用之（消除 `"a2a"` vs `startswith("a2a_")` 失配）；组标识展开覆盖真实工具名 `get_remote_agent_cards`/`call_remote_agent`。
- skill 禁用语义统一（P2-10）：model.call 与 tool catalog 一致——运行中 Run 引用的 skill 被禁用时，两处都降级为"无 skill 继续"并发一条 run 级警告通知（不再一处静默一处炸）。

## K3-6 能力判定单源（P2-11）

- search provider 枚举与"可用/降级"判定收敛到 `search/providers.py` 单点导出（`available_providers()`、`resolve(settings)`）；`capability_service.py:181-199` 与 `agent_tool_catalog` 的 `"none"/"bing_html"` 字符串特判改消费该单源。
- `requires_approval` 推导（agent_tool_catalog.py:100-104 硬编码规则）移入 `ToolExecutionPolicy.requires_approval()` 方法。

## K3-7 取消与进度（P2-12）

- tool.call 取消传播：handler 捕获 `CancelledError` 时调用工具的 `on_cancel` 钩子（BaseTool 默认 no-op；shell 工具实现 kill 进程、browser 工具关闭页面）后再抛。
- model.call 接入 `context.report_progress`（每 N 个流式 chunk 上报一次 token 计数）——最小接线，供 K4 的进度表/SSE 消费；完整流式 UI 留 backlog。

## 测试与验收

- 新增：契约 v2 三例、快照漂移例（definitions 后禁 skill → invoke 得 tool error）、启动交叉断言的红/绿例、Vision 工具出现在 AGENT 目录、UNTRUSTED 包裹快照测试、skill []/None 语义表驱动测试、a2a 组展开测试。
- grep 断言：`build_default_tools` 零引用、生产代码无 `if tool_name ==` 特判、activity 类型字面量只在 activity_types.py。
- 全量单测 + `make lint` + i18n/quality gate（新通知键按既有 contracts 流程登记）。
