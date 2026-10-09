# Web Operator

[简体中文](web-operator.zh-CN.md)

Web Operator is an Agent Run with an immutable ownership declaration and exact
hostname boundary. It uses the normal browser Activity, approval, sandbox, and
evidence protocols; there is no separate browser workflow.

## Admission

Before session creation, the user declares:

- `operator_scope`: `owned` or `third_party_saas`;
- `operator_domains`: one or more exact hostnames.

Domains are normalized to unique lowercase IDNA hostnames with trailing dots
removed. The current normalizer accepts bare hosts and URL-form inputs with
an empty or `/` path, and retains only the hostname; credentials, non-root
paths, queries, fragments, and wildcards are rejected. Ports do not become
part of the allowlist. Session metadata may update the nonempty domain list;
each admitted Run freezes its own list, so later edits do not change that Run.

## Navigation and actions

Every absolute HTTP(S) navigation, redirect, and intercepted network request
(including subresources) is checked against the exact hostname allowlist inside
the browser adapter. `about:`, `blob:`, and `data:` requests are exempt from
that route check. This host check does not perform DNS/IP or port validation;
sandbox network controls provide the separate deployment boundary. Page text is wrapped as untrusted external content before returning to
the model.

Browser reads are read-only. Navigation, click, input, and other interactive
operations have non-read-only policy and therefore require a persisted
per-invocation approval. The approval shows the frozen tool name/risk; chat
text cannot approve it. A user may inspect or take over the isolated Chromium
desktop through VNC, but VNC interaction does not forge an Activity result.

## Evidence

Operator scope/domains, Run timeline, approval actor/decision, browser Activity
status, audit chain, and authorized screenshots/artifacts are available to the
governance profile and signed evidence package. Secrets and raw browser
credentials are redacted.

Third-party SaaS selection records the user's declared scope; it does not grant
additional capability or waive external terms and legal obligations.
