# API 与 v2 内核

[English](README.md)

后端只有三个进程角色：FastAPI 命令/查询 API、异步执行内核和一次性 Alembic
迁移器。PostgreSQL 是权威事实源；Redis 只承载可丢失的唤醒/缓存，绝不保存已接收工作。

源码边界：

```text
app/contexts/identity/    用户、团队、邀请、配额、审计、通知
app/contexts/inference/   Endpoint、Model、Binding、用量、MCP 与工具适配
app/contexts/knowledge/   文件、产物、知识版本与检索
app/kernel/domain/        纯命令、事件、Reducer 与工作流
app/kernel/application/   命令、Effect、Timer、保留与重建服务
app/kernel/infrastructure/postgres/  Journal、Claim 与 Projection
app/composition/          显式 API/内核对象图
app/interfaces/           保留的 HTTP 契约
```

绿色数据库只有一个 Alembic base/head：`0001greenfield`。运行角色使用签名的
事务级授权上下文和强制 RLS。事件、审计、治理版本不可变；物理清除是独立的签名
系统操作。

```bash
uv sync --all-groups
uv run alembic upgrade head
uv run ruff check --config ../ruff.toml app core tests
uv run lint-imports
uv run pytest -q
```

进程入口为 `./run.sh`、`./execution-kernel.sh` 和 `./migrate.sh`。
