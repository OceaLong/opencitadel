#!/usr/bin/env bash
# Capture host memory/swap/container metrics before and after tuning.
# Usage: bash deploy/scripts/verify-host-health.sh [before|after]
set -euo pipefail

PHASE="${1:-snapshot}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${OUT_DIR:-/tmp/my-manus-health}"
OUT_FILE="${OUT_DIR}/health-${PHASE}-${STAMP}.txt"

mkdir -p "${OUT_DIR}"

{
  echo "=== MyManus host health (${PHASE}) @ ${STAMP} UTC ==="
  echo
  echo "--- free -h ---"
  free -h
  echo
  echo "--- swapon --show ---"
  swapon --show || true
  echo
  echo "--- vmstat 1 5 (si/so = swap in/out pages per second) ---"
  vmstat 1 5
  echo
  echo "--- docker stats --no-stream ---"
  if command -v docker >/dev/null 2>&1; then
    docker stats --no-stream 2>/dev/null || echo "docker stats unavailable"
  else
    echo "docker not installed"
  fi
  echo
  echo "--- sandbox containers (manus-sandbox-*) ---"
  if command -v docker >/dev/null 2>&1; then
    docker ps -a --filter "name=manus-sandbox-" --format "table {{.Names}}\t{{.Status}}\t{{.Size}}" 2>/dev/null || true
  fi
  echo
  echo "--- sysctl vm.swappiness ---"
  sysctl vm.swappiness
  echo
  echo "Saved to ${OUT_FILE}"
} | tee "${OUT_FILE}"

echo
echo "Compare before/after snapshots in ${OUT_DIR}:"
echo "  - Memory used should stay below ~80% at idle"
echo "  - vmstat si/so should be 0 after tuning (no swap thrashing)"
echo "  - Disk read IO on cloud monitor should drop from ~178MB/s baseline"
