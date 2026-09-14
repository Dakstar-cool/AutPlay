#!/usr/bin/env bash
# Use the same immutable media tools as the acquisition runtime image.
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP must identify the runner scratch directory}"
: "${GITHUB_PATH:?GITHUB_PATH must identify the runner path export file}"
media_image=mwader/static-ffmpeg:8.1.2@sha256:33f770f812cbfc3de96c547157fc9faf8bd95a36481753439ffa761045167585
tool_directory=$(mktemp -d "$RUNNER_TEMP/autplay-ffmpeg.XXXXXX")
container_id=""
cleanup() {
  if [[ -n "$container_id" ]]; then
    docker rm "$container_id" >/dev/null
  fi
}
trap cleanup EXIT

# This container is never started; copy only the two verified image binaries.
container_id=$(docker create "$media_image")
for tool in ffmpeg ffprobe; do
  docker cp "$container_id:/$tool" "$tool_directory/$tool"
  chmod 755 "$tool_directory/$tool"
  version=$("$tool_directory/$tool" -version)
  if [[ "$version" != "$tool version 8.1.2 "* ]]; then
    echo "Unexpected $tool version" >&2
    exit 1
  fi
  printf '%s\n' "${version%%$'\n'*}"
done
printf '%s\n' "$tool_directory" >> "$GITHUB_PATH"
