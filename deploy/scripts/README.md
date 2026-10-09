[English](README.md) · [简体中文](README.zh-CN.md)

# Deploy Scripts

Shell utilities for **production host preparation** on Ubuntu servers running OpenCitadel via Docker Compose.

## Scripts

| Script                                           | Purpose                                                                   |
| ------------------------------------------------ | ------------------------------------------------------------------------- |
| [`host-tune.sh`](host-tune.sh)                   | Kernel sysctl, swap, Docker log rotation for 16 GB single-node production |
| [`verify-host-health.sh`](verify-host-health.sh) | Capture memory, swap, and container metrics before/after tuning           |

## Usage

Run on the target server as root (or with sudo):

```bash
# Before tuning — baseline snapshot
bash deploy/scripts/verify-host-health.sh before

# Apply host tuning (swap, sysctl, Docker log rotation)
sudo bash deploy/scripts/host-tune.sh

# After tuning — compare snapshot
bash deploy/scripts/verify-host-health.sh after
```

Output defaults to `/tmp/opencitadel-health/health-{phase}-{timestamp}.txt`.

`host-tune.sh` defaults to a 4 GiB `/swapfile` (override `SWAP_SIZE_GB`/`SWAP_FILE`), sets `somaxconn=65535`, `tcp_max_syn_backlog=65535`, and `swappiness=10`, and rotates JSON logs at 100 MB with 3 files. It restarts a running Docker daemon; run it in a maintenance window. It does not set container memory/CPU quotas; Compose and Runtime Policy configure those. The snapshot script records metrics without tuning or capacity acceptance; `OUT_DIR` overrides its destination.

## Related

- [Production deployment](../../docs/operations/deployment.md) — memory tuning and sandbox quotas
- [Architecture evolution](../../docs/architecture/architecture-evolution.md) — scale-out path
