[English](README.md) · [简体中文](README.zh-CN.md)

# 仓库脚本

仓库根目录下的运维与文档维护脚本。

## 脚本列表

| 脚本 | 用途 |
|------|------|
| [`quickstart.sh`](quickstart.sh) | 首次体验：创建 `.env`、构建 `opencitadel-sandbox`、启动 Compose 栈 |
| [`check-docs.sh`](check-docs.sh) | CI 文档检查：双语配对、索引覆盖、过期内容防护 |

## 用法

```bash
# 推荐首次运行（亦可 make quickstart）
bash scripts/quickstart.sh

# 非交互模式（CI / 无 TTY）
QUICKSTART_NONINTERACTIVE=1 bash scripts/quickstart.sh

# 文档一致性检查（提交文档 PR 前）
./scripts/check-docs.sh
```

## 相关文档

- [10 分钟自托管教程](../docs/tutorials/01-self-host-10-minutes.zh-CN.md)
- [文档维护清单](../docs/MAINTENANCE_CHECKLIST.zh-CN.md)
- [部署脚本](../deploy/scripts/README.zh-CN.md) — 生产主机调优
