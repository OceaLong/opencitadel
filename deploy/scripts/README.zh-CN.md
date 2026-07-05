[English](README.md) · [简体中文](README.zh-CN.md)

# 部署脚本

在 Ubuntu 服务器上通过 Docker Compose 运行 OpenCitadel 时的**生产主机准备**工具。

## 脚本列表

| 脚本 | 用途 |
|------|------|
| [`host-tune.sh`](host-tune.sh) | 16 GB 单节点生产的内核 sysctl、swap、Docker daemon 调优 |
| [`verify-host-health.sh`](verify-host-health.sh) | 调优前后采集内存、swap 与容器指标 |

## 用法

在目标服务器上以 root（或 sudo）执行：

```bash
# 调优前 — 基线快照
bash deploy/scripts/verify-host-health.sh before

# 应用主机调优（swap、sysctl、Docker 限制）
sudo bash deploy/scripts/host-tune.sh

# 调优后 — 对比快照
bash deploy/scripts/verify-host-health.sh after
```

输出默认写入 `/tmp/opencitadel-health/health-{phase}-{timestamp}.txt`。

## 相关文档

- [生产部署](../../docs/operations/deployment.zh-CN.md) — 内存调优与沙箱配额
- [架构演进](../../docs/architecture/architecture-evolution.zh-CN.md) — 扩展路径
