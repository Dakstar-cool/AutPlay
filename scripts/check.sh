#!/usr/bin/env bash
set -euo pipefail

server_only=0
if [[ "${1:-}" == "--server-only" ]]; then
  server_only=1
elif [[ $# -gt 0 ]]; then
  echo "usage: bash scripts/check.sh [--server-only]" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="deploy/compose/compose.yaml"
compose_test_file="deploy/compose/compose.test.yaml"
compose_project="autplay-p02-$$"
compose_touched=0
previous_test_database_url="${AUTPLAY_TEST_DATABASE_URL-}"
had_test_database_url=0
if [[ -v AUTPLAY_TEST_DATABASE_URL ]]; then
  had_test_database_url=1
fi

cleanup_compose() {
  local cleanup_status=0
  if [[ $had_test_database_url -eq 1 ]]; then
    export AUTPLAY_TEST_DATABASE_URL="$previous_test_database_url"
  else
    unset AUTPLAY_TEST_DATABASE_URL
  fi
  if [[ $compose_touched -eq 1 ]]; then
    docker compose -p "$compose_project" -f "$compose_file" -f "$compose_test_file" \
      down --volumes --remove-orphans || cleanup_status=1
  fi
  if [[ -n "$(docker ps -a --filter "label=com.docker.compose.project=$compose_project" --format '{{.ID}}')" ]] || \
     [[ -n "$(docker volume ls --filter "label=com.docker.compose.project=$compose_project" --format '{{.Name}}')" ]] || \
     [[ -n "$(docker network ls --filter "label=com.docker.compose.project=$compose_project" --format '{{.Name}}')" ]]; then
    echo "Disposable Compose resources remain after cleanup" >&2
    cleanup_status=1
  fi
  return "$cleanup_status"
}

finish() {
  local status=$?
  trap - EXIT
  set +e
  cleanup_compose
  if [[ $? -ne 0 ]]; then
    status=1
  fi
  exit "$status"
}

trap finish EXIT
cd "$repo_root"

if [[ $server_only -eq 1 ]]; then
  bash scripts/bootstrap.sh --server-only
else
  bash scripts/bootstrap.sh
fi

uv lock --project server --check
uv run --project server --frozen python -c "import autplay"
uv run --project server --frozen ruff check --config server/pyproject.toml server
uv run --project server --frozen ruff format --check --config server/pyproject.toml server
uv run --project server --frozen mypy --config-file server/pyproject.toml server/src server/tests
dependency_tree_json="$(uv tree --project server --frozen --universal --format json --preview-features json-output)"
prohibited_packages="$(printf '%s' "$dependency_tree_json" | uv run --project server --frozen python -c '
import json
import re
import sys

document = json.load(sys.stdin)
pattern = re.compile(r"^(cupy|jax|jaxlib|tensorflow|torch|torchvision|torchaudio|nvidia($|-)|cuda($|-)|onnxruntime($|-)|transformers$|scikit-learn$)")
names = sorted({entry.get("name", "") for entry in document["resolution"].values() if pattern.match(entry.get("name", ""))})
print(", ".join(names))
')"
if [[ -n "$prohibited_packages" ]]; then
  echo "CPU dependency graph contains prohibited GPU or ML packages: $prohibited_packages" >&2
  exit 1
fi

if [[ $server_only -eq 0 ]]; then
  ./gradlew "-Dorg.gradle.java.home=$JAVA_HOME" --no-daemon --console=plain lintDebug testDebugUnitTest assembleDebug
fi

if [[ -n "$(docker ps -a --filter "label=com.docker.compose.project=$compose_project" --format '{{.ID}}')" ]] || \
   [[ -n "$(docker volume ls --filter "label=com.docker.compose.project=$compose_project" --format '{{.Name}}')" ]] || \
   [[ -n "$(docker network ls --filter "label=com.docker.compose.project=$compose_project" --format '{{.Name}}')" ]]; then
  echo "Refusing to reuse non-empty disposable Compose project $compose_project" >&2
  exit 1
fi

docker compose -p "$compose_project" -f "$compose_file" -f "$compose_test_file" config --quiet
compose_touched=1
docker compose -p "$compose_project" -f "$compose_file" -f "$compose_test_file" up --detach --wait
database_ready=0
database_versions=""
last_database_error="PostgreSQL final server did not become ready"
for ((attempt = 1; attempt <= 30; attempt++)); do
  if docker compose -p "$compose_project" -f "$compose_file" -f "$compose_test_file" logs --no-color postgres 2>&1 | \
      grep -Fq "PostgreSQL init process complete; ready for start up."; then
    if extension_output="$(docker compose -p "$compose_project" -f "$compose_file" -f "$compose_test_file" exec -T postgres \
        psql -U autplay -d autplay -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1)"; then
      if database_versions="$(docker compose -p "$compose_project" -f "$compose_file" -f "$compose_test_file" exec -T postgres \
          psql -U autplay -d autplay -tA -v ON_ERROR_STOP=1 \
          -c "SELECT current_setting('server_version') || '|' || extversion FROM pg_extension WHERE extname = 'vector';" 2>&1)" && \
          [[ "$database_versions" =~ ^18\.4.*\|0\.8\.6$ ]]; then
        database_ready=1
        break
      fi
      last_database_error="Unexpected PostgreSQL/pgvector versions: $database_versions"
    else
      last_database_error="pgvector extension creation failed: $extension_output"
    fi
  fi
  if [[ "$attempt" -lt 30 ]]; then
    sleep 1
  fi
done
if [[ $database_ready -ne 1 ]]; then
  echo "$last_database_error" >&2
  exit 1
fi
echo "PostgreSQL|pgvector=$database_versions"

published_endpoint="$(docker compose -p "$compose_project" -f "$compose_file" -f "$compose_test_file" port postgres 5432)"
if [[ ! "$published_endpoint" =~ ^127\.0\.0\.1:([0-9]+)$ ]]; then
  echo "PostgreSQL test port is not a dynamic loopback endpoint" >&2
  exit 1
fi
published_port="${BASH_REMATCH[1]}"
if (( published_port < 1 || published_port > 65535 )); then
  echo "PostgreSQL test port is outside the valid range" >&2
  exit 1
fi
export AUTPLAY_TEST_DATABASE_URL="postgresql+psycopg://autplay:autplay_dev_only@127.0.0.1:${published_port}/autplay"

uv run --project server --frozen pytest -c server/pyproject.toml server/tests
