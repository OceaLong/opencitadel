[English](teams-and-workspaces.md) · [简体中文](teams-and-workspaces.zh-CN.md)

# 团队与工作区

通过团队工作区实现多用户协作。资源（会话、代码库、知识库、交付物）可归属个人或团队。

## UI 入口

- **团队列表**：`/teams`
- **团队详情**：`/teams/[id]` — 成员、邀请、工作区切换
- **接受邀请**：`/invitations/[token]`

## 工作区作用域

用户在 UI 选择团队工作区后，API 请求携带：

```
X-Workspace-Id: <team_id>
```

未携带该 Header 时，服务端使用**个人作用域**（`OwnerScope.personal(user_id)`）。

| 作用域 | Header | 资源归属 |
|--------|--------|----------|
| 个人 | （无） | `owner_user_id = 当前用户` |
| 团队 | `X-Workspace-Id` | `team_id = 工作区` |

服务端会校验 `principal.team_roles` 成员关系。

## 团队角色

| 角色 | 能力 |
|------|------|
| `OWNER` | 团队全权管理；创建邀请；调整成员角色；唯一 OWNER 时不可退出 |
| `ADMIN` | 创建邀请；管理成员（`TeamService._require_team_admin`） |
| `MEMBER` | 访问团队作用域资源；无成员管理权限 |

创建团队的用户默认为 `OWNER`。平台管理员可在 `/admin/teams` 管理团队。

## API 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/teams` | 创建团队 |
| GET | `/api/teams` | 我的团队列表 |
| GET | `/api/teams/{id}` | 团队详情 |
| GET | `/api/teams/{id}/members` | 成员列表 |
| POST | `/api/teams/{id}/invitations` | 创建邀请链接 |
| POST | `/api/teams/{id}/leave` | 退出团队 |
| PATCH | `/api/teams/{id}/members/{user_id}` | 更新成员角色（OWNER） |
| DELETE | `/api/teams/{id}/members/{user_id}` | 移除成员 |
| GET | `/api/invitations/{token}` | 预览邀请 |
| POST | `/api/invitations/{token}/accept` | 接受邀请 |

会话、代码库、知识库、文件、调度、记忆等写路由需通过 `require_non_auditor`，并遵守 `WorkspaceContext`。

## 相关文档

- [安全模型](security-model.zh-CN.md) — RBAC 与工作区作用域
- [管理、审计与合规](admin-auditor-compliance.zh-CN.md) — 平台管理操作
