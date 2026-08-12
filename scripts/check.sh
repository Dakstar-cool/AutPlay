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
compose_project="autplay-p01-smoke"
compose_touched=0

cleanup_compose() {
  if [[ $compose_touched -eq 1 ]]; then
    docker compose -p "$compose_project" -f "$compose_file" down --volumes --remove-orphans
  fi
}

trap cleanup_compose EXIT
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
uv run --project server --frozen pytest -c server/pyproject.toml server/tests

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

  if [[ -n "$(docker ps -a --filter "label=com.docker.compose.project=$compose_project" --format '{{.ID}}')" ]] || \
     [[ -n "$(docker volume ls --filter "label=com.docker.compose.project=$compose_project" --format '{{.Name}}')" ]] || \
     [[ -n "$(docker network ls --filter "label=com.docker.compose.project=$compose_project" --format '{{.Name}}')" ]]; then
    echo "Refusing to reuse non-empty disposable Compose project $compose_project" >&2
    exit 1
  fi

  docker compose -p "$compose_project" -f "$compose_file" config --quiet
  compose_touched=1
  docker compose -p "$compose_project" -f "$compose_file" up --detach --wait
  docker compose -p "$compose_project" -f "$compose_file" exec -T postgres \
    psql -U autplay -d autplay -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector;"

  database_versions="$(docker compose -p "$compose_project" -f "$compose_file" exec -T postgres \
    psql -U autplay -d autplay -tA -v ON_ERROR_STOP=1 \
    -c "SELECT current_setting('server_version') || '|' || extversion FROM pg_extension WHERE extname = 'vector';")"
  if [[ ! "$database_versions" =~ ^18\.4.*\|0\.8\.6$ ]]; then
    echo "Unexpected PostgreSQL/pgvector versions: $database_versions" >&2
    exit 1
  fi
  echo "PostgreSQL|pgvector=$database_versions"

  cleanup_compose
  compose_touched=0

  if [[ -n "$(docker ps -a --filter "label=com.docker.compose.project=$compose_project" --format '{{.ID}}')" ]] || \
     [[ -n "$(docker volume ls --filter "label=com.docker.compose.project=$compose_project" --format '{{.Name}}')" ]] || \
     [[ -n "$(docker network ls --filter "label=com.docker.compose.project=$compose_project" --format '{{.Name}}')" ]]; then
    echo "Disposable Compose resources remain after cleanup" >&2
    exit 1
  fi
fi
