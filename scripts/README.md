[English](README.md) · [简体中文](README.zh-CN.md)

# Repository Scripts

Operational and documentation maintenance scripts at the repository root.

## Scripts

| Script | Purpose |
|--------|---------|
| [`quickstart.sh`](quickstart.sh) | First-run onboarding: create `.env`, build `opencitadel-sandbox`, start Compose stack |
| [`check-docs.sh`](check-docs.sh) | CI documentation checks: bilingual pairs, index coverage, stale content guards |

## Usage

```bash
# Recommended first run (also: make quickstart)
bash scripts/quickstart.sh

# Non-interactive (CI / no TTY)
QUICKSTART_NONINTERACTIVE=1 bash scripts/quickstart.sh

# Documentation consistency (run before docs PRs)
./scripts/check-docs.sh
```

## Related

- [Self-host in 10 minutes](../docs/tutorials/01-self-host-10-minutes.md)
- [Documentation maintenance checklist](../docs/MAINTENANCE_CHECKLIST.md)
- [Deploy scripts](../deploy/scripts/README.md) — production host tuning
