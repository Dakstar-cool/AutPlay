#!/usr/bin/env bash
set -euo pipefail

server_only=0
python_environment_root=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-only)
      server_only=1
      shift
      ;;
    --python-environment-root)
      if [[ $# -lt 2 ]] || [[ -z "$2" ]]; then
        echo "--python-environment-root requires a path" >&2
        exit 2
      fi
      python_environment_root="$2"
      shift 2
      ;;
    *)
      echo "usage: bash scripts/bootstrap.sh [--server-only] [--python-environment-root PATH]" >&2
      exit 2
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ -n "$python_environment_root" ]]; then
  mkdir -p "$python_environment_root"
  python_environment_root="$(cd "$python_environment_root" && pwd -P)"
fi

sync_uv_project() {
  local project="$1"
  local environment_name="$2"
  local label="$3"
  local project_arguments=()
  if [[ -n "$project" ]]; then
    project_arguments=(--project "$project")
  fi
  (
    if [[ -n "$python_environment_root" ]]; then
      export UV_PROJECT_ENVIRONMENT="$python_environment_root/$environment_name"
    else
      unset UV_PROJECT_ENVIRONMENT || true
    fi
    uv sync "${project_arguments[@]}" --frozen --python 3.14.7
    python_version="$(
      uv run "${project_arguments[@]}" --frozen \
        python -c 'import platform; print(platform.python_version())'
    )"
    if [[ "$python_version" != "3.14.7" ]]; then
      echo "$label requires CPython 3.14.7; observed: $python_version" >&2
      exit 1
    fi
  )
}

uv_version="$(uv --version)"
if [[ ! "$uv_version" =~ ^uv\ 0\.12\.3([[:space:]]|$) ]]; then
  echo "AutPlay requires uv 0.12.3; observed: $uv_version" >&2
  exit 1
fi

uv python install 3.14.7
sync_uv_project "" "root" "AutPlay contract tooling"
sync_uv_project "server" "server" "AutPlay server"

if [[ $server_only -eq 0 ]]; then
  sync_uv_project "gpu" "gpu" "AutPlay GPU worker"
  sync_uv_project "gpu/training" "training" "AutPlay Sona training"
  sync_uv_project \
    "tools/local_music_acquisition" \
    "acquisition" \
    "AutPlay acquisition"

  : "${JAVA_HOME:?JAVA_HOME must point to the pinned JDK 17}"
  : "${ANDROID_HOME:?ANDROID_HOME must point to an SDK with platform 36.1 and Build Tools 36.1.0}"
  java_version="$("$JAVA_HOME/bin/java" -version 2>&1)"
  if [[ ! "$java_version" =~ 'openjdk version "17.0.20"' ]] || [[ ! "$java_version" =~ 'Microsoft-'[0-9]+' (build 17.0.20+8-LTS)' ]]; then
    echo "AutPlay requires Microsoft OpenJDK 17.0.20+8-LTS" >&2
    exit 1
  fi
  if [[ ! -f "$ANDROID_HOME/platforms/android-36.1/android.jar" ]] || \
     [[ ! -f "$ANDROID_HOME/build-tools/36.1.0/aapt2" ]]; then
    echo "ANDROID_HOME lacks platform 36.1 or Build Tools 36.1.0" >&2
    exit 1
  fi
  gradle_version="$(./gradlew "-Dorg.gradle.java.home=$JAVA_HOME" --no-daemon --version)"
  if ! grep -Eq '^Gradle 9\.3\.1$' <<<"$gradle_version" || \
     ! grep -Eq '^Launcher JVM:[[:space:]]+17\.0\.20 \(Microsoft 17\.0\.20\+8-LTS\)$' <<<"$gradle_version" || \
     ! grep -Eq '^Daemon JVM:.*\(from org\.gradle\.java\.home\)$' <<<"$gradle_version"; then
    echo "Gradle wrapper or pinned JDK resolution failed" >&2
    exit 1
  fi
  docker compose -f deploy/compose/compose.yaml config --quiet
fi
