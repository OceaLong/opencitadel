[English](08-ten-minute-governance-demo.md)

# 10 分钟治理演示闭环

本教程在纯 Docker Compose 上跑通 OpenCitadel 完整的治理故事——只读 Ops Patrol、人为制造一条 Finding、治理概览 Dashboard、一次经人工审批的工具调用、审计链验证，以及可下载的证据包——全程无需 Kubernetes 集群。它直接建立在 [教程 06](06-ops-patrol.zh-CN.md) 和 [教程 07](07-approved-remediation.zh-CN.md) 之上；如果想了解每一步背后的完整机制，请先阅读那两篇。

以下所有步骤都基于 `./scripts/quickstart.sh --demo`：它会拉起一个自包含的演示目标（一个内置的 `ops-console` 应用，用来扮演一个真实的内部系统）并自动完成 Seed。总耗时约 10 分钟，大部分时间花在首次 Docker 构建上。

## 开始之前

你需要：

- Docker Desktop 或 Docker Engine + Compose v2，至少 8 GB 内存；
- 端口 8088（OpenCitadel）、9099（演示用 Ops Console）、8090（Ops Collector）、8091 未被占用；
- 可选：一个 OpenAI 兼容的 LLM base URL + API key，用来让第 1 步自动注册一个可用模型（否则在第 2 步手动添加，做法与 [教程 01](01-self-host-10-minutes.zh-CN.md) 相同）。无论走哪条路径，第 6 步都需要一个具备工具调用能力的默认模型。

## 1. 启动演示技术栈

```bash
git clone https://github.com/OceaLong/opencitadel.git
cd opencitadel
./scripts/quickstart.sh --demo
```

`--demo` 会在 `local` 已有配置之上把 `patrol` 和 `demo` 合并进 `COMPOSE_PROFILES`，所以 `docker compose up -d --build` 还会一并启动 Ops Collector（`opencitadel-ops-collector`）和内置的 Ops Console（`opencitadel-ops-console`，即教程 05 退款对账用的那个应用）。两者都报告健康后，脚本会自动在 API 容器内执行 `python -m app.seed_demo`。该模块依次完成：

1. 开启 `feature_flags.enable_ops_patrol`；
2. 启用 `ops-collector` MCP Server 并写入其全部九条只读 Tool Policy（与 [Ops Patrol 运维文档](../operations/ops-patrol.zh-CN.md#注册-mcp-server) 中的载荷完全一致）；
3. 可选地注册一个演示用 LLM Endpoint/Model 并设为系统默认（见下文）；
4. 创建、验证并激活一个名为 **Demo Governance Patrol**（`demo-governance-patrol`）的自定义 Pack，含三条 Check：PostgreSQL 依赖健康、OpenCitadel API 端点健康、Ops Console 依赖健康。

每一步都是幂等的——若 Seed 失败（例如某个容器当时还没就绪），重新执行 `docker compose exec -T opencitadel-api python -m app.seed_demo`（脚本失败时会原样打印出这条命令）不会产生任何额外写操作，已存在的状态只会打印 `[skip] ...`。

**自动注册模型（可选）：** API 容器通过 Compose 的 `env_file:` 加载 `.env`，所以要在容器启动**之前**把下面四行加进 `.env`（可以趁着"Press Enter when .env is ready"的暂停时编辑，或者如果 `.env` 是上次运行留下的，提前编辑好）：

```bash
DEMO_LLM_BASE_URL=https://api.example.com/v1
DEMO_LLM_API_KEY=sk-...
DEMO_LLM_MODEL=gpt-4o-mini
DEMO_LLM_PROVIDER=openai   # 可选，默认 openai
```

只要前三项有一项缺失，`seed_demo.py` 就会完整跳过这一步（打印提示），不会去猜——之后自己在 **设置 → 模型** 里手动添加即可，做法与教程 01 第 4 步一致。

## 2. 登录并确认 Seed 结果

打开 **http://localhost:8088**，用 `.env` 中的 `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` 登录。

打开 **Ops Patrol**（`/patrols`）。应该能看到一个 Pack——**Demo Governance Patrol**，状态为 **Active**，含三条 Check。如果第 1 步设置了 `DEMO_LLM_*` 变量，**设置 → 模型** 里已经有一个名为 "Demo Model"（挂在 "Demo Endpoint" 下）的默认模型；否则现在手动添加一个——第 6 步需要一个具备工具调用能力的默认模型才能创建会话。

## 3. 立即运行——全部通过

打开该 Pack（`/patrols/{id}`），点击 **立即运行 / Run now**。前端会带上唯一的 `Idempotency-Key`，重复点击不会产生第二个 Run。等待 Run 进入终态；由于 Ops Console 容器刚启动，三条 Check 全部通过：

| Check | 探针 | 观测目标 |
|-------|------|----------|
| `dependency-health` | `dependency_status`，`primary-dependencies` | 平台自身的 PostgreSQL |
| `endpoint-health` | `http_probe`，`primary-endpoint` | OpenCitadel API 的 `/api/status` |
| `demo-console-health` | `dependency_status`，`demo-console-tcp` | 对 Ops Console 容器的 TCP 拨号 |

## 4. 人为制造一条 Finding

```bash
docker compose stop ops-console
```

回到该 Pack 再次点击 **立即运行**。这次 `demo-console-health` 会失败，Run 上出现一条 **warning** 级别的 Finding。

为什么偏偏是这条 Check，为什么是确定性失败而不是报错：容器停止后 TCP 拨号会被拒绝。Collector 的 `dependency_status` 工具在内部捕获了这次连接拒绝，仍然返回一个正常的信封——`status="ok"`、`data.healthy=false`——于是服务端断言引擎按配置对 `$.healthy eq true` 求值，判定为假，把结果落成该 Check 自身 `severity_on_fail`（这里是 `warning`）对应级别的 `FAIL`。相比之下，如果换成 `http_probe` 去探一个彻底停掉的目标，连接错误会直接向外传播，在**任何断言执行之前**就短路成一个通用的 `ERROR`——技术上也会产生 Finding，但演示不出这个 Pack 自己配置的断言/严重级别。这正是这个 Pack 的第三条 Check 选择用 `dependency_status` 探 `demo-console-tcp`、而不是用 `demo-console` HTTP 探针的原因。

继续之前先把容器恢复——第 6 步需要它处于运行状态：

```bash
docker compose start ops-console
```

## 5. 查看治理概览 Dashboard

打开 **http://localhost:8088/admin/governance**。**运维巡检趋势** 图表（Runs 与 Findings 两条曲线）此时已经反映出第 3–4 步的两次 Run。**待处理审批**、**拦截总数**、**审批结果统计** 这几块此刻仍是空的——它们要等下一步产生一次门控决策后才会有数据，值得在做完第 6 步后再回来看一眼。

## 6. 触发并批准一次受治理的工具调用

Ops Console 同时也扮演着"某个只应在受监管情况下被 Agent 触碰的内部系统"。回到首页：

1. 选择 **Web Operator** Skill。
2. 发送类似这样的提示词：*打开 http://localhost:9099 上的 Ops Console 并登录。*
3. 会话创建之前会弹出 **Web Operator 范围** 对话框。**允许域名** 保持默认（`ops-console, localhost`——`localhost` 已经覆盖这个演示目标，所以首次导航本身不会触发审批）。把 **门控配置** 设为 **严格 / Strict**，然后点击 **启动 Web Operator 会话**。

这里门控档位很关键：`standard` 只有当高风险工具调用的参数命中"关键动作"关键词表（删除/关闭/退款……）时才会门控，而 Ops Console 的登录表单不会命中任何一个。`strict` 则会**无条件**门控每一次命中风险清单的调用——`browser_click`、`browser_input` 都在清单里——所以 Agent 发起的第一次点击必然会暂停等待审批；这份确定性正是这个演示专门要求 Strict、而不是依赖关键词命中的原因。

观察会话：Agent 先陈述计划，随后第一次 `browser_click`/`browser_input` 调用会产生一张 **工具操作需要审批** 卡片（工具名 + 参数的原始 JSON 预览）。点击 **批准 / Approve**。从机制上看，这会把字面消息 `approve` 作为一条聊天消息发进会话——按钮只是帮你把这句话打出来的快捷方式——所以聊天记录里看到的是一条普通的用户发言，不是什么隐藏通道。如果 Agent 后续还会发起别的需要门控的调用，可以逐次批准，也可以用 **批准同类工具** 让本会话内同类工具不再询问。

## 7. 核验治理记录

打开 **http://localhost:8088/admin/audit**，点击 **验证链**，应该看到 **审计链完整**。浏览日志列表，打开一条查看其操作者、链序号与元数据。

打开 **http://localhost:8088/admin/compliance**。你刚才那个 Web Operator 会话会出现在 **可出证会话** 列表里（任何带 operator scope 或 gate profile 的会话都会出现），**范围**、**门控**、**证据链** 列都已经填好。点该行的 **验证链** 按钮可以做一次会话级验证。要看完整的治理档案——审批决策、工具调用链、Checkpoint、证据完整性状态——直接打开 `/admin/compliance/sessions/{sessionId}`，会话 ID 可以从这一行的链接里取，也可以从会话自己的地址栏 `/sessions/{sessionId}` 里取（如果那个标签页还开着）。

## 8. 下载证据包

还在 `/admin/compliance` 页面，点同一行的 **下载证据包 / Download ZIP**。压缩包里包含该会话的审计材料、一份文件哈希清单 `manifest.json`，以及 `chain-signature.txt`——如果想离线用 `AUDIT_SIGNING_KEY` 验证 HMAC，参见 [Ops Patrol 运维文档 — 证据验证](../operations/ops-patrol.zh-CN.md#证据验证)。

## 9. 进阶：审批制修复

到目前为止全是只读观测，加上对第三方 UI 的一次受治理的"读"——没有跑过任何自动化修复动作。Ops Patrol 的写路径（提案 → 审批 → 执行 → 复检）只对由 `k8s_*` 探针支撑的 Finding（工作负载可用性、重启突增）提供修复动作：Ops Actuator 重启/扩缩容/回滚的是真实的 Kubernetes Deployment/StatefulSet，而这套 Compose 演示 Profile 根本没有集群可供它操作——这里 Seed 出来的几条 Check（`dependency_status`/`http_probe`）按教程 06 同样的设计,天生就没有对应的自动化动作。

要完整看到这条闭环，请按 [教程 07](07-approved-remediation.zh-CN.md) 在一个一次性的 `kind` 集群上操作。同样这条流程（失败 → 提案 → 执行 → 复检）目前也是 CI 每次运行都会确定性验证的内容,通过两个独立层：一个真实的 `kind` 集群驱动真实的 Ops Actuator MCP Server,以及一次不依赖 LLM、在进程内回放提案/执行/复检状态机的验证。这两层 CI 都不覆盖审批环节本身——审批环节由契约测试与受保护环境的端到端运行覆盖。当前 CI 覆盖范围见 [Ops Patrol 运维文档](../operations/ops-patrol.zh-CN.md)。

## 下一步

- [教程 06：运行只读每日运维巡检](06-ops-patrol.zh-CN.md)
- [教程 07：审批通过后执行 Ops Patrol 修复](07-approved-remediation.zh-CN.md)
- [Ops Patrol 运维文档](../operations/ops-patrol.zh-CN.md)
