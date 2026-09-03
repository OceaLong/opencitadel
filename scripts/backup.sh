#!/usr/bin/env bash
# On-demand backup for the local docker-compose stack: a PostgreSQL custom-
# format dump plus a tarball of the MinIO data volume.
#
#   scripts/backup.sh [output-dir]        # default output-dir: ./backups
#
# Restore:
#   pg_restore -U postgres -d opencitadel --clean postgres.dump
#   tar -xzf minio-data.tar.gz -C <mounted minio volume>
#
# The Helm deployment has its own CronJob (cronjob-postgres-backup.yaml);
# this script covers the compose path, which previously had no backup story.
set -euo pipefail

PROJECT="${COMPOSE_PROJECT_NAME:-opencitadel}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${1:-backups}/${STAMP}"
mkdir -p "${OUT}"

echo "==> PostgreSQL logical dump"
docker compose --project-name "${PROJECT}" --profile local exec -T \
  opencitadel-postgres pg_dump -U postgres -Fc opencitadel \
  > "${OUT}/postgres.dump"

MINIO_VOLUME="${PROJECT}_minio_data"
if docker volume inspect "${MINIO_VOLUME}" >/dev/null 2>&1; then
  echo "==> MinIO data volume tarball (${MINIO_VOLUME})"
  docker run --rm \
    -v "${MINIO_VOLUME}:/data:ro" \
    -v "$(cd "${OUT}" && pwd):/backup" \
    alpine tar -czf /backup/minio-data.tar.gz -C /data .
else
  echo "==> MinIO volume ${MINIO_VOLUME} not found; skipping object backup"
fi

echo "==> Backup complete: ${OUT}"
ls -lh "${OUT}"
