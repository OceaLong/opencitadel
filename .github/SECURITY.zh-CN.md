[English](SECURITY.md)

# 安全策略

## 受支持版本

| 版本 | 支持情况 |
| ---- | -------- |
| 最新发布版 | :white_check_mark: |
| main 分支 | :white_check_mark: |

## 报告漏洞

**请勿在公开的 GitHub Issue 中披露安全漏洞。**

请通过以下方式私下报告：

- **GitHub Security Advisories**：[github.com/OceaLong/opencitadel](https://github.com/OceaLong/opencitadel)（推荐）
- 邮件联系仓库主页或 Release 标签中列出的维护者

请包含：

- 漏洞描述
- 复现步骤
- 影响评估（数据泄露、沙箱逃逸、认证绕过等）
- 如有，建议的修复方案

我们力争在 **48 小时内**确认收到报告，并在 **7 天内**给出初步评估。

## 范围

**在范围内：**

- 认证与授权绕过
- 沙箱隔离失效（容器逃逸、跨会话数据访问）
- 日志、API 响应或存储中的密钥/凭证泄露
- API 或沙箱服务中的 SSRF、注入与不安全的反序列化

**不在范围内：**

- 未展示完整利用链的拒绝服务
- 您自行配置的第三方 LLM 提供商或 MCP 服务器问题
- 部署层面的错误配置（弱密码、暴露 `.env` 等），且无代码层面修复
