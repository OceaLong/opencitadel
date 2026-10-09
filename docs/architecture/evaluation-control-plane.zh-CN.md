# 评测控制面

[English](evaluation-control-plane.md)

评测将不可变 Dataset、Configuration、Rubric、Suite 与 Recorded/Isolated 来源组合成
持久化 Batch。每个 Subject 与 Judge Run 都使用[执行内核](execution-kernel.zh-CN.md)。
Batch 编排、评分、人工复核、环境 Lease 与预算账务分别有持久化状态来源，均不能替代
Run Event Log。

![评测准入、执行与评分](../assets/diagrams/evaluation-control-plane.png)

## 准入与内核归属

API 校验原始 Principal 与 OwnerScope，验证不可变版本成员关系及 Resource Pin，捕获
当前策略/配置证明，保存 Command 与 Batch 状态，不调用 Provider 或执行 Case。
Kernel 持有四条关键 `EvaluationRuntime` Lane：

| Lane                    | 职责                                                                        |
| ----------------------- | --------------------------------------------------------------------------- |
| `evaluation-scheduler`  | 认领并隔离 Batch、准入 Subject Run、对账 Result、提交取消与符合条件的重试。 |
| `evaluation-reconciler` | 对账 Judge Intent 和迟到的物理事实，消费持久化人工复核 Command。            |
| `evaluation-scoring`    | 发现 Batch、重新鉴权、计算 Rule Score、调度受限 Judge Run。                 |
| `evaluation-cleanup`    | 处理 Recording/Environment 工作、清理对象存储、维护 Summary Inventory。     |

Inventory 只负责发现，不代表授权。每次 Mutation 都重新校验原始请求人、当前成员关系、
所选版本和取消状态。权限撤销在对应 Owner 内处理；关键 Lane 的非预期失败撤销 Kernel
就绪状态并请求关闭，不能让已停止的评测服务继续标记为 Ready。

Case × Configuration × Repetition 的确定性矩阵产生持久化 Slot 与 Attempt。Source
预绑定、预算绑定、Execution Slot 准备、`CreateRun` 提交与 Attempt 绑定共享准入 UoW；
出错时整笔事务回滚。幂等 Admission Key 与 Claim Fencing 防止重复调度。Subject 复用
配置指定的 `agent` 或 `ask` Family，Source Type 为 `evaluation_recorded_case` 或
`evaluation_isolated_case`。Judge 使用 `ask`，Source Type 为 `evaluation_judge`，
`parent_run_id` 绑定 Subject，不增加 Run Family。

取消提交普通 Run Command 并等待正式结果。自动重试只允许已失败的基础设施 Attempt、
尚有重试额度且不存在未知副作用的情况。重试创建独立且有关联的 Attempt，不改写原 Run
或证据。Batch View 分别保留执行、评分、人工复核和清理状态。

## Recorded 与 Isolated 来源

Recorded 准入在 Run Admission 前绑定可信不可变 Recording 与所选 Tool Contract。
专用 Replay Adapter 消费 Recording；绑定缺失、不匹配、撤销或不可用时 Fail-Closed，
不能静默回退到真实外部写操作。Coverage 区分已消费、未使用与不匹配的调用。

Isolated 准入绑定已批准的 Environment Version、精确 Case Slot、请求人、Lease
Generation、Configuration Fingerprint 与 Policy Digest。`EnvironmentRuntime`
构建所选测试 Catalog，不使用普通 Session Catalog。调用前重新检查 Lease 状态/过期
时间/代次、当前工具策略、目标 Contract、凭据及所需审批。外部测试目标使用绑定 Lease
的注册 Executor；Sandbox 调用使用隔离 Adapter。Broker 部署参见
[评测环境](../evaluation-environments.md)。

Lease 状态持久化为 `allocated → preparing → ready → leased → cleaning →
verified_clean`；结果不确定或验证失败进入 `quarantine`。只有 `verified_clean` 可复用。
隔离中的 Lease 必须获得明确管理员修复授权才能重新清理；取消或进程退出不能证明清理
成功。

## 物理模型调用与预算

Budget Namespace 固定 Suite、原始请求人、所选 Candidate、策略版本、来源身份以及
Batch Token/Money 限额。独立 Execution Slot 限制 Subject/Judge/Global/User 的 Run
并发。物理模型调用在共享 Dispatch 边界完成容量与预算预留，覆盖 Provider Attempt
及 Fallback Candidate；统计逻辑 Case 完成次数不足以核算物理调用用量。

发送前，持久化 Reservation 与 `execution_model_dispatches` 将物理 Call Identity、
Configuration、Price Snapshot、Lineage 和准入证明绑定。可信 Usage 及原始完成证据
产生正式结算和 Usage 记录。Token 或价格证据缺失时保留 Unknown，不能把缺失值记为零。
外部结果不确定时保留预算占用，并在未解决前阻止不安全的重试、评分和归档。关闭 Namespace
阻止新准入，不删除已有账务义务。迟到的原始证据可由 Kernel Authority 结算原调用，
不能伪造新的用户操作或物理调用。

## 评分与稳定完成切面

Rule、Model、Human Score 分别追加不可变来源版本，显式绑定 Result、Subject Run、
Rubric 与 Evaluation Revision。缺失或不可评维度保留 Null。Required Rule 通过率以
有效完成的 Case 为分母，同时报告 Missing/Excluded 数量；可选 Rule 不扩大覆盖率。
人工复核不覆盖 Model 来源证据，也不结算未知物理副作用。

Judge Intent 捕获固定的已授权材料和受限协议。Judge 无 Tool、外部检索、Session
Memory 或外部知识。JSON 输出必须符合适用维度与已提供 Evidence ID，材料字段均按
不可信数据处理。模型组装与物理发送前再次校验当前 Intent、请求人、Pin 和调用权限。

Subject 进入终态后，迟到 Usage 仍可能推进投影版本。
`DBEvaluationJudgeRepository.current()` 刷新 Batch 绑定的 Attempt 与 Result Revision，
对精确 Run/Source/Owner/Team 投影行取得 `FOR SHARE` 锁，读取当前 Completed Projection，
并在同一 UoW 重新验证评分资格，以保持稳定切面，不伪造 Revision。

自动 Rule/Judge Batch Consumer 只捕获 `ScoringProjectionAdvanced`：请求人、成员关系、
版本、Accepted Receipt、终态、取消状态和未解决副作用检查全部通过后，真实 Completed
Projection 的版本前进。Consumer 延后该 Candidate，下一轮重新读取真实新切面。
公开请求中的过期 Candidate 仍被拒绝。版本倒退、投影缺失/失败、Receipt 被拒绝、权限
撤销或未解决副作用均不属于这一延后条件。

## 保留策略与实现状态

归档改变资源发现状态，保留不可变证据与 Pin。公开 Archive Guard 拒绝 Busy 或未解决
的资源。清理归属明确的 Docker 基础设施与删除预算账务、评分、复核、执行证据是不同
操作；容器清理成功不能解决未知模型调用。

本文描述已实现的架构边界，不表示 AC21 完整参考容量验收已通过。该验收仍需满足参考
环境、完整多轮采集/清理交接以及原始工作负载标准；组件测试或较小规模运行不能替代。

## 实现锚点

- Composition 与 Lane：`api/app/composition/evaluation.py`、
  `api/app/composition/kernel.py`、`api/app/application/evaluation/runtime.py`。
- 准入与调度：`api/app/application/evaluation/scheduler.py`、
  `budget_admission.py`、`replay_admission.py`、`environment_admission.py`。
- Isolated 与 Replay Dispatch：`api/app/application/evaluation/environment_runtime.py`、
  `replay_runtime.py`；`api/app/domain/evaluation/environment.py`。
- 物理账务：`api/app/infrastructure/execution/budget_dispatch.py`、
  `api/app/infrastructure/repositories/db_evaluation_budget_repository.py`。
- 受限 Judge 与稳定评分：`api/app/domain/evaluation/judge_protocol.py`、
  `scoring.py`；`api/app/application/evaluation/judge_service.py`、
  `rule_scoring_service.py`；`api/app/infrastructure/repositories/db_evaluation_judge_repository.py`、
  `db_evaluation_score_repository.py`。
- 人工复核与归档：`api/app/application/evaluation/review_consumer.py`、
  `archive_service.py`；`api/app/infrastructure/repositories/db_evaluation_archive_repository.py`。
