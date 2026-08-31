[English](README.md) · [简体中文](README.zh-CN.md)

# 确定性全栈验收

OpenCitadel 的发布阻断端到端套件是自包含的。它通过真实公共 API 与 UI，贯穿
PostgreSQL Command Admission、执行内核、Provider Adapter、正式投影、审批、SSE、
动态 Sandbox、Ops Collector 与关停清理。它不依赖外部模型密钥、预置产品数据或
应用侧测试模式。

## 唯一推荐命令

在仓库根目录运行：

```bash
./scripts/run-acceptance-e2e.sh --disposable
```

Runner 会分配唯一 Compose Project、回环端口、产品资源命名空间和 Sandbox 前缀；
构建七个生产镜像及仅验收使用的推理 Provider，启动所需 Profile，按依赖顺序运行
全部 Playwright Project，写入证据，并且只排空与其完整归属身份一致的资源。

仅在排障时运行局部 Project；局部 Manifest 会明确标记为 partial，不能替代发布门禁：

```bash
./scripts/run-acceptance-e2e.sh --playwright-project patrol-admin --disposable
```

## 产品覆盖

| Project | 产品边界 |
| --- | --- |
| `identity` | 登录/退出、团队、邀请、工作区隔离、匿名拒绝 |
| `control-plane` | 推理 Endpoint/Model/Probe/Binding/Capability 与 Runtime Policy CAS/历史/恢复 |
| `resources` | 知识库与代码库构建、发布、版本固定及降级关闭失败 |
| `execution` | Agent/Ask、SSE、审批、拒绝、取消与 Sandbox 排空 |
| `patrol-admin` | 正式 Patrol 验证/执行/证据/准入、管理、合规、移动端与键盘可访问性 |

`contracts/acceptance-evidence.schema.json` 是必需验收 ID 的唯一事实源。任一必需 ID
缺失、重复、跳过、中断或失败，zero-skip Reporter 都会使运行失败。

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
- [执行内核切换证据](../docs/architecture/execution-kernel-cutover-evidence.zh-CN.md)
- [Ops Patrol 运维](../docs/operations/ops-patrol.zh-CN.md)
