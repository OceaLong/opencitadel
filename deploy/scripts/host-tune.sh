#!/usr/bin/env bash
# Host tuning for MyManus single-node production (16GB RAM).
# Run on the Ubuntu server as root: sudo bash deploy/scripts/host-tune.sh
set -euo pipefail

SWAP_SIZE_GB="${SWAP_SIZE_GB:-4}"
SWAP_FILE="${SWAP_FILE:-/swapfile}"
SYSCTL_CONF="/etc/sysctl.conf"
DOCKER_DAEMON_JSON="/etc/docker/daemon.json"

log() { echo "[host-tune] $*"; }

ensure_sysctl() {
  local key="$1"
  local value="$2"
  if grep -qE "^${key}[[:space:]]*=" "$SYSCTL_CONF" 2>/dev/null; then
    sed -i "s|^${key}[[:space:]]*=.*|${key} = ${value}|" "$SYSCTL_CONF"
  else
    echo "${key} = ${value}" >> "$SYSCTL_CONF"
  fi
}

log "Applying kernel network and memory tuning..."
ensure_sysctl "net.core.somaxconn" "65535"
ensure_sysctl "net.ipv4.tcp_max_syn_backlog" "65535"
ensure_sysctl "vm.swappiness" "10"
sysctl -p >/dev/null

log "Configuring ${SWAP_SIZE_GB}G swap at ${SWAP_FILE} (OOM safety net, not primary memory)..."
if swapon --show | grep -q "${SWAP_FILE}"; then
  log "Swap already active at ${SWAP_FILE}, skipping creation."
elif [ -f "${SWAP_FILE}" ]; then
  chmod 600 "${SWAP_FILE}"
  mkswap "${SWAP_FILE}" >/dev/null
  swapon "${SWAP_FILE}"
else
  fallocate -l "${SWAP_SIZE_GB}G" "${SWAP_FILE}" 2>/dev/null || dd if=/dev/zero of="${SWAP_FILE}" bs=1M count=$((SWAP_SIZE_GB * 1024)) status=progress
  chmod 600 "${SWAP_FILE}"
  mkswap "${SWAP_FILE}" >/dev/null
  swapon "${SWAP_FILE}"
  if ! grep -qF "${SWAP_FILE}" /etc/fstab; then
    echo "${SWAP_FILE} none swap sw 0 0" >> /etc/fstab
  fi
fi

log "Configuring Docker log rotation..."
mkdir -p /etc/docker
if [ -f "${DOCKER_DAEMON_JSON}" ]; then
  python3 - <<'PY'
import json
from pathlib import Path

path = Path("/etc/docker/daemon.json")
data = json.loads(path.read_text()) if path.read_text().strip() else {}
data.setdefault("log-driver", "json-file")
data.setdefault("log-opts", {})
data["log-opts"]["max-size"] = "100m"
data["log-opts"]["max-file"] = "3"
path.write_text(json.dumps(data, indent=2) + "\n")
PY
else
  cat > "${DOCKER_DAEMON_JSON}" <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
EOF
fi

if systemctl is-active --quiet docker; then
  log "Restarting Docker to apply daemon.json..."
  systemctl restart docker
fi

log "Done. Run deploy/scripts/verify-host-health.sh to capture baseline metrics."
