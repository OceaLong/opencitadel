# Deploy OpenCitadel v2

[简体中文](deployment.zh-CN.md)

## Topology

Production runs API, execution kernel, UI, PostgreSQL/pgvector, Redis, object
storage, the sandbox egress proxy, and a sandbox lifecycle implementation.
Compose uses the authenticated Docker broker. Helm lets the kernel create
per-Run Pods through a namespace-scoped ServiceAccount.

No in-place data upgrade exists. Back up anything you still need, deploy an
empty PostgreSQL database, and run only `0001greenfield`.

## Required controls

- Generate independent values for `API_KEY_SECRET`, `AUDIT_SIGNING_KEY`,
  `JWT_SECRET`, `SESSION_SECRET`, `DATABASE_AUTHORIZATION_SIGNING_SECRET`,
  `SANDBOX_BROKER_TOKEN`, and `SANDBOX_TOKEN_SEED`.
- Set distinct PostgreSQL admin, migration, API, and kernel credentials.
- Set `REDIS_PASSWORD`, object-storage credentials, and a strong bootstrap
  administrator password.
- Keep `COOKIE_SECURE=true`; set exact `FRONTEND_BASE_URL` and
  `OAUTH_REDIRECT_BASE` values.
- Trust only exact reverse-proxy CIDRs in `TRUSTED_PROXY_CIDRS`.
- Keep `OUTBOUND_ALLOWED_PORTS` and private-host allowlists minimal.
- Restrict Docker socket access to the broker; the API and kernel never mount it.

Copy `.env.example`, replace every placeholder, then:

```bash
docker compose --profile local build opencitadel-sandbox
docker compose --profile local up -d --build
curl -fsS http://localhost:8088/api/health/ready
```

To intentionally destroy local v2 data and start empty:

```bash
bash scripts/quickstart.sh --reset-data
```

The command removes only the selected Compose project's containers and named
volumes. Never use a host-wide Docker prune as an OpenCitadel reset.

## Kubernetes

```bash
helm lint deploy/helm/opencitadel --set-file secrets=production-secrets.yaml
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  -f production-values.yaml
```

The migration init container must complete before API readiness. Confirm the
kernel ServiceAccount can only manage Pods in the release namespace. Keep the
sandbox NetworkPolicy enabled: it permits DNS and the egress proxy only, while
the trusted kernel can reach sandbox API/CDP ports.

## Verification and recovery

- API lifecycle: `/api/health/live`, `/api/health/ready`.
- Kernel lifecycle: `python -m app.execution_kernel_health readiness`.
- Rebuild projections from the append-only journal after suspected drift.
- PostgreSQL backups are the authoritative recovery asset; Redis may be lost.
- Rotate credentials through new encrypted control-plane records; never edit
  event or audit history.
