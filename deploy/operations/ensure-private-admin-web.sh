#!/usr/bin/env bash
set -euo pipefail

# Idempotent boot check for the private production Admin Web container.
container="autplay-production-tailnet-admin"
probe="http://127.0.0.1:18779/admin/static/admin-v2.css"

for attempt in {1..60}; do
  if /usr/bin/docker info >/dev/null 2>&1; then
    break
  fi
  if (( attempt == 60 )); then
    echo "Docker did not become available" >&2
    exit 1
  fi
  sleep 2
done

if ! /usr/bin/docker container inspect "$container" >/dev/null 2>&1; then
  echo "Private Admin Web container is missing" >&2
  exit 1
fi

if [[ "$(/usr/bin/docker inspect --format '{{.State.Running}}' "$container")" != "true" ]]; then
  /usr/bin/docker start "$container" >/dev/null
fi

for attempt in {1..30}; do
  if [[ "$(/usr/bin/curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 4 "$probe" 2>/dev/null || true)" == "200" ]]; then
    echo "Private Admin Web is ready"
    exit 0
  fi
  if (( attempt == 30 )); then
    echo "Private Admin Web did not become ready" >&2
    exit 1
  fi
  sleep 2
done
