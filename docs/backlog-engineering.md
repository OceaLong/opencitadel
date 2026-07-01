#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Long-term engineering backlog (Phase 4)."""

# 沙箱隔离加固
# - Docker 层：seccomp / AppArmor / cap_drop / no-new-privileges
# - 长期评估 gVisor runtime 或 Firecracker 微 VM 沙箱网关

# 工程质量
# - 为核心 session_routes（chat / SSE / VNC）补集成测试
# - 引入 GitHub Actions CI（lint + pytest + 镜像构建）
# - 后端补 ruff / mypy；前端补 Vitest 组件测试

# 可观测性
# - Grafana 仪表盘：browser_vision_fallback_total、agent token、队列深度
