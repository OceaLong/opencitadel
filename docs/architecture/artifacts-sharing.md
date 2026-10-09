[English](artifacts-sharing.md) · [简体中文](artifacts-sharing.zh-CN.md)

# Artifacts & Public Sharing

Session artifacts (reports, HTML previews) and time-limited public share links.

## What are artifacts?

Artifacts are versioned outputs produced during Agent sessions:

- **doc** — Markdown reports (`.md`)
- **web** — HTML previews (sanitized before render)

Object storage (COS/MinIO) uses unique upload keys under
`artifacts/{session_id}/{artifact_id}/uploads/{upload_id}`. Committed
`version_refs` map artifact version numbers to these immutable object keys;
durable upload intents allow cleanup to distinguish unfinished uploads from
referenced versions.

![Artifact versions and sharing](../assets/diagrams/artifacts-sharing.png)

## UI and API

| Action                 | API                                | UI                        |
| ---------------------- | ---------------------------------- | ------------------------- |
| List session artifacts | `GET /api/sessions/{id}/artifacts` | Session artifact panel    |
| Get artifact metadata  | `GET /api/artifacts/{id}`          | Artifact workbench        |
| Get content            | `GET /api/artifacts/{id}/content`  | Preview / download        |
| Create share link      | `POST /api/artifacts/{id}/share`   | Share button              |
| Revoke share link      | `DELETE /api/artifacts/{id}/share` | Revoke action             |
| Public view            | `GET /api/share/artifact/{token}`  | `/share/artifact/[token]` |

Private routes require authenticated session with `WorkspaceContext` scope (personal or team).

Each stored version records a committed provenance receipt and content digest.
Writes without a producer record explicit unknown/unavailable provenance rather
than inventing a Run or Step binding.
Execution detail requests select an explicit artifact version and bind it to
`run_id`, `step_id`, and the historical `at` cut. Content reads are bounded and
return `truncated` / `next_cursor`; unavailable provenance or revoked ownership
cannot silently fall back to a newer artifact version.

## Share link behavior

- Default TTL: **168 hours** (7 days) — `create_share_link(ttl_hours=168)`
- Token: URL-safe random string stored on the artifact row
- Expired or missing tokens return 404 on public route
- Re-sharing generates a new token and expiry
- Revocation: `DELETE /api/artifacts/{id}/share` clears the token and expiry immediately
- Share creation and revocation write audit records in the same transaction

The public share path serves the currently stored artifact content; it is not
an immutable Run replay or historical analysis/export capture.

Public URL format: `https://your-domain/share/artifact/{token}` (UI route; API is `/api/share/artifact/{token}`).

## HTML safety

The server's `sanitize_html_for_preview()` strips script tags and quoted inline
event handlers before delivery. The shared `SafeArtifactPreview` component then rebuilds a static
document from an element allowlist: it removes attributes, external resources,
styles, forms, scripts, SVG/MathML, and navigation. Its `srcDoc` iframe uses
`sandbox=""`, `referrerPolicy="no-referrer"`, and a `default-src 'none'` CSP.
Execution detail, the artifact workbench, and the public share view use the same
preview boundary.

## Related documentation

- [Security model](security-model.md) — artifact scope and iframe policy
- [Teams and workspaces](teams-and-workspaces.md) — team-scoped artifact access
