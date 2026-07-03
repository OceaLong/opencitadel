#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compliance control catalog: 等保2.0 + ISO27001:2022."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EvaluatorName = Literal[
    "auth_present",
    "rbac_present",
    "operator_scope_declared",
    "gates_present",
    "chain_intact",
    "redaction_on",
    "rollback_capable",
    "audit_logging",
    "gate_approvals",
    "tool_audit",
    "self_hosted",
    "evidence_export",
    "session_isolation",
    "encryption_at_rest",
    "input_untrusted",
    "central_admin",
    "timestamp_integrity",
    "privileged_access_control",
    "monitoring_present",
    "crypto_controls",
]


@dataclass(frozen=True)
class Control:
    framework: str
    control_id: str
    title: str
    requirement: str
    capability: str
    evaluator: EvaluatorName


CONTROLS: list[Control] = [
    # --- 等保2.0 ---
    Control("djbh2.0", "8.1.4.1", "身份鉴别", "应对登录用户进行身份标识和鉴别", "JWT/OAuth 登录与会话管理", "auth_present"),
    Control("djbh2.0", "8.1.4.2", "访问控制", "应实现主体对客体的访问控制", "RBAC(admin/user/auditor)+团队工作区", "rbac_present"),
    Control("djbh2.0", "8.1.4.3", "安全审计", "应启用安全审计功能", "audit_logs + agent_tool_invoke", "audit_logging"),
    Control("djbh2.0", "8.1.4.4", "数据完整性", "应采用校验技术保证重要数据完整性", "HMAC 审计证据链", "chain_intact"),
    Control("djbh2.0", "8.1.4.5", "数据保密性", "应采用密码技术保证重要数据保密", "API Key 加密 + 审计参数脱敏", "redaction_on"),
    Control("djbh2.0", "8.1.4.6", "剩余信息保护", "应保证鉴别信息及敏感数据残留清除", "检查点回滚与会话清理", "rollback_capable"),
    Control("djbh2.0", "8.1.4.7", "个人信息保护", "应仅采集和保存业务必需个人信息", "operator_scope 声明与域白名单", "operator_scope_declared"),
    Control("djbh2.0", "8.1.4.8", "入侵防范", "应提供对输入内容的检测与过滤", "页面不可信输入 + 门控", "input_untrusted"),
    Control("djbh2.0", "8.1.4.9", "恶意代码防范", "应安装防恶意代码软件或采取等效措施", "沙箱隔离执行环境", "session_isolation"),
    Control("djbh2.0", "8.1.4.10", "资源控制", "应限制单个用户/进程资源使用", "用户配额与并发任务限制", "rbac_present"),
    Control("djbh2.0", "8.2.1.1", "集中管控", "应划分不同管理员角色并实现集中管控", "Admin 后台 + 审计导出", "central_admin"),
    Control("djbh2.0", "8.2.1.2", "审计数据保护", "应对审计记录进行保护", "不可篡改证据链 + 证据包", "evidence_export"),
    Control("djbh2.0", "8.2.1.3", "审计记录留存", "审计记录应留存不少于六个月", "PostgreSQL 持久化审计", "audit_logging"),
    Control("djbh2.0", "8.2.1.4", "可信验证", "可基于可信根对系统启动/运行进行验证", "Plan/Tool 审批门控", "gates_present"),
    # --- ISO27001:2022 Annex A ---
    Control("iso27001", "A.5.15", "访问控制", "应建立并实施访问控制规则", "RBAC + 团队作用域", "rbac_present"),
    Control("iso27001", "A.5.16", "身份管理", "应管理用户全生命周期", "用户注册/禁用/角色管理", "auth_present"),
    Control("iso27001", "A.5.18", "访问权限", "应Provisioning/Deprovisioning 访问权限", "管理员分配角色与配额", "privileged_access_control"),
    Control("iso27001", "A.5.23", "云服务使用", "应管理云服务使用风险", "完全私有化自托管部署", "self_hosted"),
    Control("iso27001", "A.5.28", "证据收集", "应收集并保留信息作为证据", "会话证据包 ZIP+PDF", "evidence_export"),
    Control("iso27001", "A.5.33", "记录保护", "应保护日志不被篡改", "HMAC 审计链校验", "chain_intact"),
    Control("iso27001", "A.8.2", "特权访问", "应限制并管理特权访问", "HITL 门控 + 审批留痕", "gate_approvals"),
    Control("iso27001", "A.8.5", "安全认证", "应实施安全认证机制", "JWT + CSRF + API Key", "auth_present"),
    Control("iso27001", "A.8.15", "日志记录", "应记录活动并保护日志", "audit_logs 全链路", "audit_logging"),
    Control("iso27001", "A.8.16", "监控活动", "应监控网络/系统/应用异常", "Admin 用量/审计概览", "monitoring_present"),
    Control("iso27001", "A.8.17", "时钟同步", "应同步信息处理系统时钟", "审计 created_at 时间戳", "timestamp_integrity"),
    Control("iso27001", "A.8.24", "密码学使用", "应定义并实施密码学规则", "HMAC 链 + API Key 加密", "crypto_controls"),
    Control("iso27001", "A.8.25", "安全开发生命周期", "应建立安全开发规则", "Web Operator 写操作门控", "gates_present"),
    Control("iso27001", "A.8.26", "应用安全要求", "应定义应用安全要求", "operator_scope + gate_profile", "operator_scope_declared"),
    Control("iso27001", "A.8.28", "安全编码", "应安全编码", "工具参数脱敏", "redaction_on"),
    Control("iso27001", "A.8.29", "安全测试", "应在开发/发布中测试安全", "证据链校验 API", "chain_intact"),
    Control("iso27001", "A.8.31", "环境隔离", "应分离开发/测试/生产", "Docker/K8s 沙箱隔离", "session_isolation"),
    Control("iso27001", "A.8.32", "变更管理", "应控制变更", "检查点回滚能力", "rollback_capable"),
    Control("iso27001", "A.8.33", "测试信息", "应保护测试信息", "审计 args 脱敏", "redaction_on"),
    Control("iso27001", "A.8.34", "审计测试保护", "审计测试期间保护运行系统", "只读审计员角色", "rbac_present"),
]
