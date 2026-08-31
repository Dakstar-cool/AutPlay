#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: ./server-control.sh <start|stop|status|logs|bootstrap-owner|invite-browser|fingerprint> [arguments]" >&2
  echo "  bootstrap-owner <display-name> [device-name]" >&2
  echo "  invite-browser <owner-uuid>" >&2
}

[[ $# -ge 1 ]] || { usage; exit 2; }
action="$1"
shift
bundle_root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
state_dir="${AUTPLAY_SERVER_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/autplay-server}"
env_file="$state_dir/server.env"
project_file="$state_dir/project-name.txt"
[[ -f "$env_file" && -f "$project_file" ]] || { echo "server state is missing; run install-server.sh first" >&2; exit 1; }
project_name="$(tr -d '\r\n' <"$project_file")"
[[ "$project_name" =~ ^[a-z0-9][a-z0-9_-]{2,62}$ ]] || { echo "STATE_PROJECT_INVALID" >&2; exit 1; }
installer_env="$bundle_root/server-installer.env"
release_version="$(sed -n 's/^RELEASE_VERSION=//p' "$installer_env" | head -n 1)"
image_tag="$(sed -n 's/^IMAGE_TAG=//p' "$installer_env" | head -n 1)"
release_tag="$(sed -n 's/^RELEASE_TAG=//p' "$installer_env" | head -n 1)"
source_commit="$(sed -n 's/^SOURCE_COMMIT=//p' "$installer_env" | head -n 1)"
[[ "$release_version" =~ ^[0-9]+[.][0-9]+[.][0-9]+(-rc[.][0-9]+)?$ ]] || { echo "invalid release version" >&2; exit 1; }
[[ "$image_tag" =~ ^autplay-server:v[0-9]+[.][0-9]+[.][0-9]+(-rc[.][0-9]+)?$ ]] || { echo "invalid image tag" >&2; exit 1; }
[[ "$release_tag" =~ ^v[0-9]+[.][0-9]+[.][0-9]+(-rc[.][0-9]+)?$ && "$source_commit" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid release identity" >&2; exit 1; }
state_image="$(sed -n 's/^AUTPLAY_SERVER_IMAGE=//p' "$env_file" | head -n 1)"
state_release_tag="$(sed -n 's/^AUTPLAY_RELEASE_TAG=//p' "$env_file" | head -n 1)"
state_source_commit="$(sed -n 's/^AUTPLAY_SOURCE_COMMIT=//p' "$env_file" | head -n 1)"
state_bind_host="$(sed -n 's/^AUTPLAY_MOBILE_BIND_HOST=//p' "$env_file" | head -n 1)"
[[ "$state_image" == "$image_tag" && "$state_release_tag" == "$release_tag" && "$state_source_commit" == "$source_commit" ]] || {
  echo "STATE_VERSION_MISMATCH" >&2
  exit 1
}
is_private_ipv4() {
  local value="$1" octets=() octet
  IFS=. read -r -a octets <<<"$value"
  [[ "${#octets[@]}" -eq 4 ]] || return 1
  for octet in "${octets[@]}"; do
    [[ "$octet" =~ ^(0|[1-9][0-9]{0,2})$ ]] && (( 10#$octet <= 255 )) || return 1
  done
  (( 10#${octets[0]} == 10 )) ||
    (( 10#${octets[0]} == 192 && 10#${octets[1]} == 168 )) ||
    (( 10#${octets[0]} == 172 && 10#${octets[1]} >= 16 && 10#${octets[1]} <= 31 ))
}
is_private_ipv4 "$state_bind_host" || { echo "STATE_ORIGIN_MISMATCH" >&2; exit 1; }
compose=(docker compose --project-name "$project_name" --env-file "$env_file"
  --file "$bundle_root/compose.yaml"
  --file "$bundle_root/compose.runtime.yaml"
  --file "$bundle_root/compose.admin-local.yaml"
  --file "$bundle_root/compose.release.yaml"
  --profile runtime)

case "$action" in
  start) "${compose[@]}" up --no-build --wait ;;
  stop) "${compose[@]}" down --remove-orphans ;;
  status) "${compose[@]}" ps ;;
  logs) "${compose[@]}" logs --no-color --tail 200 ;;
  bootstrap-owner)
    [[ $# -ge 1 ]] || { usage; exit 2; }
    display_name="$1"
    device_name="${2:-$(hostname)}"
    "${compose[@]}" exec -T api autplay-admin bootstrap-owner --display-name "$display_name" --device-name "$device_name" --platform OTHER --app-version "$release_version"
    ;;
  invite-browser)
    [[ $# -eq 1 ]] || { usage; exit 2; }
    "${compose[@]}" exec -it api autplay-admin web-session-invite --user-id "$1"
    ;;
  fingerprint)
    identity_key="$state_dir/secrets/profile-identity-p256.pem"
    [[ -f "$identity_key" ]] || { echo "persistent server identity is missing" >&2; exit 1; }
    docker run --rm --network none --mount "type=bind,src=$identity_key,dst=/identity.pem,readonly" --entrypoint python "$image_tag" -c 'from cryptography.hazmat.primitives import serialization; from cryptography.hazmat.primitives.asymmetric import ec; import hashlib; key=serialization.load_pem_private_key(open("/identity.pem","rb").read(),password=None); assert isinstance(key,ec.EllipticCurvePrivateKey) and isinstance(key.curve,ec.SECP256R1); print(hashlib.sha256(key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest())'
    ;;
  *) usage; exit 2 ;;
esac
