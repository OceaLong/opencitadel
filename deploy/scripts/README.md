[English](README.md) · [简体中文](README.zh-CN.md)

# Deploy Scripts

Shell utilities for **production host preparation** on Ubuntu servers running OpenCitadel via Docker Compose.

## Scripts

| Script | Purpose |
|--------|---------|
| [`host-tune.sh`](host-tune.sh) | Kernel sysctl, swap, Docker daemon tuning for 16 GB single-node production |
| [`verify-host-health.sh`](verify-host-health.sh) | Capture memory, swap, and container metrics before/after tuning |

## Usage

Run on the target server as root (or with sudo):

```bash
# Before tuning — baseline snapshot
bash deploy/scripts/verify-host-health.sh before

# Apply host tuning (swap, sysctl, Docker limits)
sudo bash deploy/scripts/host-tune.sh

# After tuning — compare snapshot
bash deploy/scripts/verify-host-health.sh after
```

Output defaults to `/tmp/opencitadel-health/health-{phase}-{timestamp}.txt`.

## Related

- [Production deployment](../../docs/operations/deployment.md) — memory tuning and sandbox quotas
- [Architecture evolution](../../docs/architecture/architecture-evolution.md) — scale-out path
