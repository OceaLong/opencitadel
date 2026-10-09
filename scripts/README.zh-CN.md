[English](README.md) · [简体中文](README.zh-CN.md)

# 仓库脚本

仓库根目录下的运维与文档维护脚本。

## 脚本列表

| 脚本                                                                                                                         | 用途                                                                                                           |
| ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| [`backup.sh`](backup.sh) / [`backup_tool.py`](backup_tool.py)                                                                | 停写后的 Compose 数据库与对象备份，含完整性 Manifest                                                           |
| [`verify-backup.sh`](verify-backup.sh) / [`restore.sh`](restore.sh)                                                          | 离线校验与全新卷恢复演练；参见[操作手册](../docs/operations/deployment.zh-CN.md#本地-compose-备份与隔离恢复)   |
| [`quickstart.sh`](quickstart.sh)                                                                                             | 首次体验：创建 `.env`、构建 `opencitadel-sandbox`、启动 Compose 栈                                             |
| [`check-docs.sh`](check-docs.sh)                                                                                             | CI 文档检查：双语配对、索引覆盖、过期内容防护                                                                  |
| [`run-acceptance-e2e.sh`](run-acceptance-e2e.sh)                                                                             | 管理隔离的全栈验收、证据 Manifest 与精确 Label 清理                                                            |
| [`run-patrol-fixtures.sh`](run-patrol-fixtures.sh)                                                                           | 创建一次性 kind 集群，运行/重置 20 个 Patrol 案例，验证 Collector 只读权限并评分                               |
| [`score_patrol_fixtures.py`](score_patrol_fixtures.py)                                                                       | 校验机器可读 Patrol Fixture 结果；通常由 Runner 调用                                                           |
| [`drive_remediation_fixture.py`](drive_remediation_fixture.py)                                                               | 修复 Fixture 的确定性无 LLM 测试驱动；`PATROL_RUN_REMEDIATION_FIXTURE=true` 时由 `run-patrol-fixtures.sh` 调用 |
| [`verify_evidence_package.py`](verify_evidence_package.py)                                                                   | 离线校验证据包 ZIP（Manifest HMAC 签名 + 逐文件 SHA-256 摘要）                                                 |
| [`seed_execution_visualization.py`](seed_execution_visualization.py) / [`execution_capacity/`](execution_capacity/README.md) | 归属历史容量来源构建、有限实时负载与参考机证据工具；完整 AC21 运行门禁仍未完成                                 |
| [`acceptance/`](acceptance/)                                                                                                 | `run-acceptance-e2e.sh` 背后的 Python 辅助模块：所有权安全编排（`runner.py`）与证据 Manifest（`manifest.py`）  |

## 用法

```bash
# 推荐首次运行（亦可 make quickstart）
bash scripts/quickstart.sh

# 非交互模式（CI / 无 TTY）
QUICKSTART_NONINTERACTIVE=1 bash scripts/quickstart.sh

# 文档一致性检查（提交文档 PR 前）
./scripts/check-docs.sh

# 完整验收尝试（AC21 前提见 e2e/README.zh-CN.md）；只删除本次归属 Volume
./scripts/run-acceptance-e2e.sh --disposable

# 破坏性 Fixture，但仅在脚本创建的一次性 kind 集群内
./scripts/run-patrol-fixtures.sh
```

严禁对共享 Context 单独执行 Patrol Fixture Setup Manifest。Runner 强制要求 `kind-opencitadel-patrol-*` Context 与一次性 Namespace Label，并会删除集群；仅排障时显式设置 `PATROL_KEEP_DEMO_CLUSTER=true` 才保留。

验收 Runner 是唯一受支持的全栈 E2E 入口。它分配唯一 Compose Project 与 Run ID，
校验回环端口，生成 `tmp/acceptance/<run-id>/manifest.json`，并在检查或删除 Docker
资源前要求 Project/Run Label 精确一致。不带 `--disposable` 时只保留 Project Volume
与产品历史用于排障；Container、Network 和动态 Sandbox 始终排空。不得用宽泛的
`docker system prune` 或固定 Compose Project Name 替代它。

完整或 `execution` 验收的 AC21 还要求 Report/Fixture 输入与受信任的私有
`OfflineProofContext`。当前 CLI 没有构造该 Context，完整参考机采集/复用仍未完成。
将产品测试结果解释为发布门禁通过前，请先查看[验收状态](../e2e/README.zh-CN.md#ac21-容量前提与当前状态)。

## 相关文档

- [10 分钟自托管教程](../docs/tutorials/01-self-host-10-minutes.zh-CN.md)
- [文档维护清单](../docs/MAINTENANCE_CHECKLIST.zh-CN.md)
- [确定性全栈验收](../e2e/README.zh-CN.md)
- [部署脚本](../deploy/scripts/README.zh-CN.md) — 生产主机调优
- [Ops Patrol 故障实验室](../deploy/patrol-demo/README.zh-CN.md)
