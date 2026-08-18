#!/usr/bin/env bash
set -euo pipefail

server_only=0
if [[ "${1:-}" == "--server-only" ]]; then
  server_only=1
elif [[ $# -gt 0 ]]; then
  echo "usage: bash scripts/bootstrap.sh [--server-only]" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

uv_version="$(uv --version)"
if [[ ! "$uv_version" =~ ^uv\ 0\.12\.3([[:space:]]|$) ]]; then
  echo "AutPlay requires uv 0.12.3; observed: $uv_version" >&2
  exit 1
fi

uv python install 3.14.7
uv sync --frozen --python 3.14.7
harness_python_version="$(uv run --frozen python -c 'import platform; print(platform.python_version())')"
if [[ "$harness_python_version" != "3.14.7" ]]; then
  echo "AutPlay harness requires CPython 3.14.7; observed: $harness_python_version" >&2
  exit 1
fi
uv run --frozen autplay-codex --version
uv sync --project server --frozen --python 3.14.7
python_version="$(uv run --project server --frozen python -c 'import platform; print(platform.python_version())')"
if [[ "$python_version" != "3.14.7" ]]; then
  echo "AutPlay server environment requires CPython 3.14.7; observed: $python_version" >&2
  exit 1
fi

if [[ $server_only -eq 0 ]]; then
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
