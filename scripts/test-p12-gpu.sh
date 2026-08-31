#!/usr/bin/env bash
set -euo pipefail

selector="${1:-auto}"
selector_pattern='^(auto|uuid:GPU-[A-Za-z0-9-]{8,100}|pci:((([0-9A-Fa-f]{4}|[0-9A-Fa-f]{8}):)?[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}[.][0-7])|index:[0-9]{1,3})$'
if [[ ! "$selector" =~ $selector_pattern ]]; then
  echo "usage: bash scripts/test-p12-gpu.sh [auto|uuid:<id>|pci:<id>|index:<n>]" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="autplay-gpu-worker:p12-qual"
container_prefix="autplay-p12-gpu-gate-$$"
containers=()

cleanup() {
  local name
  for name in "${containers[@]:-}"; do
    docker rm --force "$name" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

run_gate() {
  local phase="$1"
  shift
  local name="${container_prefix}-${phase}"
  containers+=("$name")
  docker run \
    --name "$name" \
    --rm \
    --network none \
    --read-only \
    --tmpfs /tmp:size=64m,mode=1777 \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --gpus all \
    --env "AUTPLAY_GPU_DEVICE_SELECTOR=$selector" \
    --entrypoint autplay-ml-gpu \
    "$image" \
    "$@"
}

cd "$repo_root"
docker build --progress plain --file gpu/Dockerfile --tag "$image" .
run_gate list --list-devices
run_gate select --select-device
run_gate config --check-config

echo "P12 Docker GPU static/device gate passed for selector=$selector"
echo "A reviewed model artifact and benchmark dataset are still required for A-030 metrics."
