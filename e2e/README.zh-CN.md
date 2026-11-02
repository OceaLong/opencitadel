[English](README.md) · [简体中文](README.zh-CN.md)

# 确定性全栈验收

OpenCitadel 的端到端产品套件通过真实公共 API 与 UI，贯穿
PostgreSQL Command Admission、执行内核、Provider Adapter、正式投影、审批、SSE、
动态 Sandbox、Ops Collector 与关停清理。它不依赖外部模型密钥、预置产品数据或
应用侧测试模式。完整发布门禁还要求 AC21 参考机容量证据；当前限制见下文。

## 唯一推荐命令

在仓库根目录运行：

```bash
./scripts/run-acceptance-e2e.sh --disposable
```

Runner 会分配唯一 Compose Project、回环端口、产品资源命名空间和 Sandbox 前缀；
构建生产服务与 Sandbox 镜像及仅验收使用的推理 Provider，启动所需 Profile，按依赖顺序运行
全部 Playwright Project，写入证据，并且只排空与其完整归属身份一致的资源。
`e2e/tmp/` 中保留的临时场景不进入正式验收收集范围。

Execution 与资源用例在完成全部历史、成果、引用与比较读取后，只退休其精确浏览器
Page 创建的会话。公共会话软删除会释放关联 Sandbox；成功后完成持久清理记录，
失败则保留 pending 义务供最后重放。Bootstrap strict 来源和跨用例共享的 Patrol 会话
保留至消费者完成。生产 Sandbox 配额与内存门槛保持原值。重试证据观察器用
`ACCEPTANCE_PROJECT_ID` 匹配本轮 Lifecycle 与容器完整 Label，兼容默认及显式项目名。

未被取消时，完整运行和 `execution` 运行会在 Playwright 结束后校验当次原生 strict 报告与恢复回执，
即使浏览器检查失败也会尝试校验。只有 Producer 有效且恢复得到证明，才执行
`api/tests/app/integration/test_execution_visualization_closed_loop.py` 的六项断言。
Runner 传入当次证据目录与身份，保留 `strict-pytest.xml`，强制六项全部通过且零跳过。
浏览器、strict 与容量错误都保留在 Manifest，完整命令仍以非零退出；消费者通过不能
消除先前失败。
CI API Job 与 `make test-api` / `make test-api-strict` 只排除这个消费者文件；
它依赖验收生产者，不能使用普通 API 测试数据库或历史证据运行。

SIGINT/SIGTERM 会中断 Runner 当前所属命令进程组（包括 CLI 后代）；取消宽限五秒后，
若该进程组仍活动，则向同一组发送 SIGKILL。Runner 记录中断失败，进入清理阶段后
禁用取消探测，让所属清理命令正常运行；Sandbox 事件采集继续保留生命周期证据，
清理按完整归属 Label 执行。取消不会转换成通过；缺失或未执行的 strict 消费者不能
宣称已验证。对忽略中断的任意外部进程，不承诺固定结束期限。

仅在排障时运行局部 Project；局部 Manifest 会明确标记为 partial，不能替代发布门禁：

```bash
./scripts/run-acceptance-e2e.sh --playwright-project patrol-admin --disposable
```

## 第一阶段专项验收

专项入口运行真实写入丢失回执后的查询、契约漂移后重新审批两条浏览器整链，以及初始化和清理：

```bash
./scripts/run-acceptance-e2e.sh --disposable --phase1-browser
```

该入口写入 `phase1-manifest.json`，要求四个浏览器节点和六个严格消费者全部通过，零跳过、零重试。
仅验收环境的 shell 查询器核对原物理回执与目标；生产 shell 操作继续使用人工结算契约。
专项入口不准备 AC21 容量证据，不能代表完整发布验收通过。

## 产品覆盖

| Project         | 产品边界                                                                                              |
| --------------- | ----------------------------------------------------------------------------------------------------- |
| `identity`      | 登录/退出、团队、邀请、工作区隔离、匿名拒绝                                                           |
| `control-plane` | 推理 Endpoint/Model/Probe/Binding/Capability 与 Runtime Policy CAS/历史/恢复                          |
| `resources`     | 仅关键词知识库构建、READY 发布与版本固定；损坏 PDF 触发 DOCUMENT_PARTIAL，正常文本仍可检索            |
| `execution`     | Agent/Ask、SSE、审批、拒绝、取消、Sandbox 排空、执行工作台、评测批次/治理、分析、比较、导出与容量证据 |
| `patrol-admin`  | 正式 Patrol 验证/执行/证据/准入、管理、合规、移动端与键盘可访问性                                     |

`contracts/acceptance-evidence.schema.json` 是必需验收 ID 的唯一事实源。任一必需 ID
缺失、重复、跳过、中断或失败，zero-skip Reporter 都会使运行失败。

Bootstrap 会探测并绑定 Chat 和 Embedding 模型，但新安装策略下只有 Chat 变为可用。
Embedding 与重排保持关闭，直到显式开启对应消费者。资源场景始终使用基础策略，
不修改全局增强功能配置。

## AC21 容量前提与当前状态

AC21 校验保留的参考机实测，而不是确定性 Provider 冒烟测试。完整运行或 `execution`
运行要求同时提供 `ACCEPTANCE_CAPACITY_REPORT` 与
`ACCEPTANCE_CAPACITY_FIXTURE_MANIFEST`。消费者接受 Report Version 4、Role Schema 3
和 Fixture Manifest Version 1，Validation Receipt 为 Version 2。成功校验还必须有
Runner 持有的具体 `OfflineProofContext`，其中包括原始 Ledger、受信任镜像位置与
仅存在内存中的原始签名材料。公共 Report 或路径本身不提供该权威。

当前 Shell/CLI 入口没有构造这个私有 Context。参考机协调器也会在真正接通 C2c
清理/复用门禁前拒绝第二个物理 Round；完整 Linux 参考机的 Native Scheduler/Collector
与有界离线消费者尚未闭环。因此推荐命令会执行产品检查与清理，如实保留 AC21 失败并
以非零退出，目前不能建立 AC21 完整发布门禁通过的结论。功能 Project 与六项 strict
消费者必须依据本轮真实 Producer、经验证的恢复回执与保留 Artifact 核验。
`--list` 收集、Mock、辅助/单元测试、局部运行、保留截图与归属资源残留归零都不能
声明全栈成功，也不能替代完整参考机测量。
必须保留失败的容量 Receipt 与 Manifest。参见[容量工具](../scripts/execution_capacity/README.md)
和[参考机协议](../scripts/execution_capacity/REFERENCE.md)。

## 确定性推理边界

`fixtures/inference-provider/` 实现生产 Adapter 使用的窄化 OpenAI 兼容协议，响应完全由
请求确定。Provider 只在 Compose 内网可达，以非 root、只读文件系统、丢弃全部 Capability
运行，并且不接收数据库、存储、OAuth、Docker 或生产 Provider Credential。

该服务只存在于 Compose `acceptance` Profile，不进入 Helm、Kustomize、Quickstart、
生产设置或 Release 镜像矩阵。外部 Provider 检查若单独保留，只是兼容性 Canary，
不计入必需验收覆盖。

## 证据与清理

每次运行写入 `tmp/acceptance/<run-id>/manifest.json`，以及日志、JUnit、Playwright JSON、
失败 Trace/截图、镜像 Digest、Migration Head、服务健康/重启、Sandbox 生命周期与残留计数。
成功前会用 `contracts/acceptance-evidence.schema.json` 校验 Manifest。

公共清理会保留不可变的已发布历史。AC18 在录制发布时，持久绑定本次团队的创建者、
Owner、录制作业、来源 Run 与固定结果元数据。只有精确的 `409 resource_pinned` 响应，
以及当前团队/Owner 成员关系、明确团队 Scope 下的录制状态与结果读取全部核验成功，
才会将该 Journal 条目标记为 `retained-published-history`。结果接口会验证录制当前固定
引用的可用性。证据明确记录 `physically_deleted=false`，以及永久保留团队 Scope 与
Owner 访问权的义务，不声称团队已删除或归档。未绑定的冲突、身份/Owner/版本变化、
录制不可用、服务端错误或批次拒绝归档，仍是待处理的清理义务并使清理失败。
显式绑定的团队若意外已删除或不存在，也会保留 pending，因为无法核验其应永久保留的历史。
Docker 残留计数只描述本次所属基础设施，与保留的产品历史分别记录；`--disposable`
会在记录这些公共 API 处置证据后，移除本次创建的 Volume。

资源归属要求所有适用 Label 同时一致：

- `com.docker.compose.project=<project-id>`；
- `com.opencitadel.acceptance.project=<project-id>`；
- `com.opencitadel.acceptance.run=<run-id>`；
- 动态 Sandbox 还必须具有 `opencitadel.io/sandbox=true` 和 Run Scope 名称前缀。

不带 `--disposable` 时，为本地排障保留产品历史与 Project Volume，并在 Manifest 中报告；
Container、Network 和动态 Sandbox 仍必须排空。带 `--disposable` 时，本次创建的 Volume
也必须归零。Runner 不执行宽泛 Docker 清理，绝不触碰无关 Project 或 `voc-*` 资源。

失败时查看运行目录中的 `failure_reason`、`logs/stack.log` 与 Playwright Artifact；
证据捕获和清理仍会执行。开发 Runner 故障路径时，参见
`scripts/acceptance/runner.py` 中受保护的 Fault 选项。

## 直接使用 Playwright

`npm test` 只适用于对已准备好的验收栈进行局部排障；它不负责栈隔离、Bootstrap、
证据校验与清理，因此不是发布门禁。

```bash
cd e2e
npm ci
npx playwright install chromium
npm run test:meta
```

## 相关文档

- [仓库脚本](../scripts/README.zh-CN.md)
- [生产部署](../docs/operations/deployment.zh-CN.md)
- [Ops Patrol 运维](../docs/operations/ops-patrol.zh-CN.md)
