[English](README.md) · [简体中文](README.zh-CN.md)

# 仓库脚本

- `quickstart.sh` 创建本地 `.env`、生成相互独立的密钥、构建沙箱镜像并启动核心服务。
- `quickstart.sh --reset-data` 会显式删除本项目容器和命名卷，再从空数据库启动。
- `check-docs.sh` 检查必要的中英文文档，并阻止已退役产品名重新进入可执行部署面。

这些脚本都不会执行 Git 暂存或提交。
