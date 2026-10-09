[English](teams-and-workspaces.md) · [简体中文](teams-and-workspaces.zh-CN.md)

# Teams and Workspaces

Multi-user collaboration via team workspaces. Resources (sessions, knowledge bases, artifacts) can be owned by a user or scoped to a team.

## UI entry

- **Teams list**: `/teams`
- **Team detail**: `/teams/[id]` — members, invitations, workspace switch
- **Accept invitation**: `/invitations/[token]` — preview, sign in, or register and join

## Invitation and account onboarding

OpenCitadel has two invitation types:

| Type            | Issuer                              | Link                         | Purpose              |
| --------------- | ----------------------------------- | ---------------------------- | -------------------- |
| Platform invite | Platform admin `/admin/invitations` | `/register?invite_token=...` | Platform access only |
| Team invite     | Team owner/admin `/teams/[id]`      | `/invitations/{token}`       | Join a team          |

Team invites accept an optional **invitee email** (hybrid security model):

- **With email**: New users register with a password on the invite page and join in one step; existing users sign in and accept (email must match)
- **Without email**: Only users who already have a platform account can accept (open link; trusted environments)

The login page supports safe `?redirect=` return paths; OAuth login also carries `redirect` and `team_invite_token`.

![team invitation](../assets/diagrams/team-invitation.png)

## Workspace scoping

When a user selects a team workspace in the UI, API requests include:

```
X-Workspace-Id: <team_id>
```

If the header is omitted, the server uses **personal scope** (`OwnerScope.personal(user_id)`).

| Scope    | Header           | Resource ownership             |
| -------- | ---------------- | ------------------------------ |
| Personal | (none)           | `owner_user_id = current user` |
| Team     | `X-Workspace-Id` | `team_id = workspace`          |

The server validates `principal.team_roles` before accepting a team workspace.

![workspace switch](../assets/diagrams/workspace-switch.png)

`WorkspaceSwitcher` (`ui/src/components/workspace-switcher.tsx`) calls the client data provider, which stores the team id in a per-user localStorage key and mirrors `ACTIVE_WORKSPACE_KEY` for API headers, publishes the new data scope, and performs a **full page reload**. Scoped caches and in-flight reads use the authenticated user/workspace identity; the switcher clears a selection when that user no longer belongs to the team.

![team owner scope](../assets/diagrams/team-owner-scope.png)

## Team roles

| Role     | Capabilities                                                                             |
| -------- | ---------------------------------------------------------------------------------------- |
| `OWNER`  | Full team admin; create invitations; change member roles; cannot leave if sole owner     |
| `ADMIN`  | Create invitations and remove members; role changes and team dissolution require `OWNER` |
| `MEMBER` | Access team-scoped resources; no member management                                       |

Team creators default to `OWNER`. Platform admins can manage teams from `/admin/teams`.

## API routes

| Method | Path                                | Description                                                         |
| ------ | ----------------------------------- | ------------------------------------------------------------------- |
| POST   | `/api/teams`                        | Create team                                                         |
| GET    | `/api/teams`                        | List my teams                                                       |
| GET    | `/api/teams/{id}`                   | Team detail                                                         |
| GET    | `/api/teams/{id}/members`           | List members                                                        |
| POST   | `/api/teams/{id}/invitations`       | Create invitation link (optional `email`)                           |
| POST   | `/api/teams/{id}/leave`             | Leave team                                                          |
| DELETE | `/api/teams/{id}`                   | Dissolve team (OWNER); `transfer_to_owner` by default, or `cascade` |
| PATCH  | `/api/teams/{id}/members/{user_id}` | Update member role (OWNER)                                          |
| DELETE | `/api/teams/{id}/members/{user_id}` | Remove member                                                       |
| GET    | `/api/invitations/{token}`          | Preview invitation (public)                                         |
| POST   | `/api/invitations/{token}/register` | Register and join (public; email-bound invites only)                |
| POST   | `/api/invitations/{token}/accept`   | Accept invitation (authenticated)                                   |

Authenticated business routes inherit `enforce_auditor_read_only`: auditors may use GET/HEAD/OPTIONS but cannot submit mutations. Selected write routes also use `require_non_auditor`; owner-scoped resources use `WorkspaceContext`. Team deletion disposes or transfers its resources and records the chosen strategy in audit history.

## Related documentation

- [Security model](security-model.md) — RBAC and workspace scoping
- [Admin, auditor & compliance](admin-auditor-compliance.md) — platform admin operations
