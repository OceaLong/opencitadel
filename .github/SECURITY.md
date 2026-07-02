[简体中文](SECURITY.zh-CN.md)

# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | -------------------- |
| latest release | :white_check_mark: |
| main branch    | :white_check_mark: |

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

Report security issues privately via:

- **GitHub Security Advisories** on [github.com/OceaLong/opencitadel](https://github.com/OceaLong/opencitadel) (preferred)
- Email the maintainers listed on the repository profile or release tags

Include:

- Description of the vulnerability
- Steps to reproduce
- Impact assessment (data exposure, sandbox escape, auth bypass, etc.)
- Suggested fix if available

We aim to acknowledge reports within **48 hours** and provide an initial
assessment within **7 days**.

## Scope

In scope:

- Authentication and authorization bypass
- Sandbox isolation failures (container escape, cross-session data access)
- Secret/credential exposure in logs, API responses, or storage
- SSRF, injection, and unsafe deserialization in API or sandbox services

Out of scope:

- Denial of service without demonstrated exploit chain
- Issues in third-party LLM providers or MCP servers you configure
- Misconfigurations in deployment (weak passwords, exposed `.env`) without a code fix
