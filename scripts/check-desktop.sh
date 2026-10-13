#!/usr/bin/env bash
set -euo pipefail

task_project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${JAVA_HOME:?JAVA_HOME must select Microsoft OpenJDK 17.0.20+8-LTS}"
task_java_version="$("$JAVA_HOME/bin/java" -version 2>&1)"
if [[ "$task_java_version" != *'17.0.20'* || "$task_java_version" != *'Microsoft'* ]]; then
  echo 'Desktop gate requires the pinned Microsoft OpenJDK 17.0.20+8-LTS' >&2
  exit 1
fi
cd "$task_project_root"
task_gradle_tasks=(
  :shared:domain:jvmTest :shared:application:jvmTest :shared:ui:jvmTest
  :apps:desktop:classes :apps:desktop:jar
)
if [[ "${1:-}" == '--package-macos' ]]; then
  if [[ "$(uname -s)" != 'Darwin' || "$(uname -m)" != 'arm64' ]]; then
    echo 'macOS packaging gate requires a native Apple Silicon Mac' >&2
    exit 1
  fi
  task_gradle_tasks+=(:apps:desktop:createDistributable :apps:desktop:packageDmg)
elif [[ $# -ne 0 ]]; then
  echo 'Usage: check-desktop.sh [--package-macos]' >&2
  exit 1
fi
bash ./gradlew "-Dorg.gradle.java.home=$JAVA_HOME" --no-daemon --console=plain \
  --max-workers=1 --dependency-verification=strict -Pautplay.desktopOnly=true \
  "${task_gradle_tasks[@]}"
