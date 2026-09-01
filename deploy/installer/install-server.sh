#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: ./install-server.sh --bind-host <RFC1918 IPv4> [--state-dir <path>] [--project-name <name>] [--no-start]" >&2
}

bundle_root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
state_dir="${XDG_STATE_HOME:-${HOME}/.local/state}/autplay-server"
project_name="autplay-personal"
bind_host=""
start_server=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bind-host) bind_host="${2:-}"; shift 2 ;;
    --state-dir) state_dir="${2:-}"; shift 2 ;;
    --project-name) project_name="${2:-}"; shift 2 ;;
    --no-start) start_server=0; shift ;;
    *) usage; exit 2 ;;
  esac
done

[[ "$state_dir" != *$'\n'* && "$state_dir" != *$'\r'* ]] || { echo "STATE_PATH_INVALID" >&2; exit 2; }

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

if ! is_private_ipv4 "$bind_host"; then
  echo "bind host must be a concrete RFC1918 IPv4 address; never use 0.0.0.0" >&2
  exit 2
fi
if ! [[ "$project_name" =~ ^[a-z0-9][a-z0-9_-]{2,62}$ ]]; then
  echo "project name must contain 3-63 lowercase letters, digits, underscores, or hyphens" >&2
  exit 2
fi

state_dir="$(realpath -m -- "$state_dir")"
bundle_root="$(realpath -e -- "$bundle_root")"
if [[ "$state_dir" == "/" || "$state_dir" == "$HOME" || "$state_dir" == "$bundle_root" || "$state_dir" == "$bundle_root/"* ]]; then
  echo "STATE_PATH_UNSAFE" >&2
  exit 2
fi
ensure_private_directory() {
  local target="$1"
  if [[ -e "$target" || -L "$target" ]]; then
    [[ -d "$target" && ! -L "$target" ]] || { echo "STATE_PATH_UNSAFE" >&2; exit 2; }
    [[ "$(stat -c '%u' -- "$target")" == "$EUID" && "$(stat -c '%a' -- "$target")" == "700" ]] || {
      echo "STATE_DIRECTORY_NOT_PRIVATE" >&2
      exit 2
    }
  else
    (umask 077; mkdir -p -- "$target")
    chmod 700 -- "$target"
  fi
}
ensure_private_directory "$state_dir"
ensure_private_directory "$state_dir/secrets"

command -v docker >/dev/null
docker version --format '{{.Server.Version}}' >/dev/null
compose_version="$(docker compose version --short)"
compose_version="${compose_version#v}"
IFS=. read -r compose_major compose_minor compose_patch _ <<<"$compose_version"
compose_patch="${compose_patch%%-*}"
if ! [[ "$compose_major" =~ ^[0-9]+$ && "$compose_minor" =~ ^[0-9]+$ && "$compose_patch" =~ ^[0-9]+$ ]] ||
  (( compose_major < 2 || (compose_major == 2 && compose_minor < 24) || (compose_major == 2 && compose_minor == 24 && compose_patch < 4) )); then
  echo "Docker Compose 2.24.4 or newer is required; found $compose_version" >&2
  exit 1
fi
installer_env="$bundle_root/server-installer.env"
[[ -f "$installer_env" ]] || { echo "installer environment manifest is missing" >&2; exit 1; }
archive_name=""
expected_hash=""
image_tag=""
source_commit=""
while IFS='=' read -r key value; do
  case "$key" in
    SERVER_ARCHIVE) archive_name="$value" ;;
    SERVER_ARCHIVE_SHA256) expected_hash="$value" ;;
    IMAGE_TAG) image_tag="$value" ;;
    SOURCE_COMMIT) source_commit="$value" ;;
  esac
done <"$installer_env"
[[ "$archive_name" =~ ^autplay-server-v[0-9]+[.][0-9]+[.][0-9]+(-rc[.][0-9]+)?[.]docker[.]tar[.]gz$ ]] || { echo "invalid archive name in installer manifest" >&2; exit 1; }
[[ "$expected_hash" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid archive hash in installer manifest" >&2; exit 1; }
[[ "$image_tag" =~ ^autplay-server:v[0-9]+[.][0-9]+[.][0-9]+(-rc[.][0-9]+)?$ ]] || { echo "invalid image tag in installer manifest" >&2; exit 1; }
[[ "$source_commit" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid source commit in installer manifest" >&2; exit 1; }
archive="$bundle_root/$archive_name"
actual_hash="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$actual_hash" == "$expected_hash" ]] || { echo "server image archive SHA-256 mismatch" >&2; exit 1; }

if [[ -f "$state_dir/project-name.txt" && "$(tr -d '\r\n' <"$state_dir/project-name.txt")" != "$project_name" ]]; then
  echo "existing state belongs to a different Compose project" >&2
  exit 1
fi
if [[ -f "$state_dir/server.env" ]]; then
  existing_bind="$(sed -n 's/^AUTPLAY_MOBILE_BIND_HOST=//p' "$state_dir/server.env" | head -n 1)"
  if [[ -n "$existing_bind" && "$existing_bind" != "$bind_host" ]]; then
    echo "existing state is bound to a different LAN address; follow the origin-change procedure" >&2
    exit 1
  fi
  existing_image="$(sed -n 's/^AUTPLAY_SERVER_IMAGE=//p' "$state_dir/server.env" | head -n 1)"
  if [[ -n "$existing_image" && "$existing_image" != "$image_tag" ]]; then
    echo "STATE_VERSION_MISMATCH" >&2
    exit 1
  fi
fi
new_secret() {
  local target="$1"
  if [[ ! -e "$target" ]]; then
    umask 077
    docker run --rm --network none --entrypoint python "$image_tag" -c 'import secrets; print(secrets.token_urlsafe(48))' >"$target"
  fi
}

docker load --input "$archive" >/dev/null
image_identity="$(docker image inspect --format '{{.Os}}|{{.Architecture}}|{{.Config.User}}|{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image_tag")"
[[ "$image_identity" == "linux|amd64|autplay:autplay|$source_commit" ]] || { echo "IMAGE_IDENTITY_MISMATCH" >&2; exit 1; }
new_secret "$state_dir/secrets/auth-signing.txt"
new_secret "$state_dir/secrets/public-access-source-hmac.txt"
new_secret "$state_dir/secrets/admin-source-hmac.txt"
new_secret "$state_dir/secrets/admin-csrf-hmac.txt"
identity_key="$state_dir/secrets/profile-identity-p256.pem"
if [[ ! -e "$identity_key" ]]; then
  umask 077
  docker run --rm --network none --entrypoint python "$image_tag" -c 'from cryptography.hazmat.primitives import serialization; from cryptography.hazmat.primitives.asymmetric import ec; import sys; sys.stdout.buffer.write(ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))' >"$identity_key"
fi
# Compose bind-mounted file secrets cannot remap UID/GID on native Linux. The closed 0700 parent
# prevents host traversal while 0444 lets the non-root container UID read the mounted files.
chmod 0444 "$state_dir/secrets/"*
mapfile -t secret_values < <(for file in "$state_dir/secrets/auth-signing.txt" "$state_dir/secrets/public-access-source-hmac.txt" "$state_dir/secrets/admin-source-hmac.txt" "$state_dir/secrets/admin-csrf-hmac.txt"; do tr -d '\r\n' <"$file"; echo; done)
[[ "${#secret_values[@]}" -eq 4 ]] || { echo "SECRET_STATE_INVALID" >&2; exit 1; }
for value in "${secret_values[@]}"; do [[ "${#value}" -ge 32 ]] || { echo "SECRET_STATE_INVALID" >&2; exit 1; }; done
[[ "$(printf '%s\n' "${secret_values[@]}" | sort -u | wc -l)" -eq 4 ]] || { echo "SECRET_STATE_INVALID" >&2; exit 1; }
identity_fingerprint="$(docker run --rm --network none --mount "type=bind,src=$identity_key,dst=/identity.pem,readonly" --entrypoint python "$image_tag" -c 'from cryptography.hazmat.primitives import serialization; from cryptography.hazmat.primitives.asymmetric import ec; import hashlib; key=serialization.load_pem_private_key(open("/identity.pem","rb").read(),password=None); assert isinstance(key,ec.EllipticCurvePrivateKey) and isinstance(key.curve,ec.SECP256R1); print(hashlib.sha256(key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest())')"
[[ "$identity_fingerprint" =~ ^[0-9a-f]{64}$ ]] || { echo "IDENTITY_STATE_INVALID" >&2; exit 1; }

env_file="$state_dir/server.env"
cat >"$env_file" <<EOF
AUTPLAY_SERVER_IMAGE=$image_tag
AUTPLAY_RELEASE_TAG=${image_tag#autplay-server:}
AUTPLAY_SOURCE_COMMIT=$source_commit
AUTPLAY_RUNTIME_AUTH_SECRET_FILE=$state_dir/secrets/auth-signing.txt
AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE=$state_dir/secrets/public-access-source-hmac.txt
AUTPLAY_RUNTIME_ADMIN_SOURCE_SECRET_FILE=$state_dir/secrets/admin-source-hmac.txt
AUTPLAY_RUNTIME_ADMIN_CSRF_SECRET_FILE=$state_dir/secrets/admin-csrf-hmac.txt
AUTPLAY_RUNTIME_PROFILE_IDENTITY_KEY_FILE=$identity_key
AUTPLAY_RUNTIME_BIND_HOST=127.0.0.1
AUTPLAY_MOBILE_BIND_HOST=$bind_host
AUTPLAY_API_PORT=8787
AUTPLAY_MOBILE_API_PORT=18787
AUTPLAY_MOBILE_STREAM_PORT=18788
AUTPLAY_PROFILE_LABEL_HINT=AutPlay local server
EOF
chmod 600 "$env_file"
printf '%s\n' "$project_name" >"$state_dir/project-name.txt"

compose=(docker compose --project-name "$project_name" --env-file "$env_file"
  --file "$bundle_root/compose.yaml"
  --file "$bundle_root/compose.runtime.yaml"
  --file "$bundle_root/compose.admin-local.yaml"
  --file "$bundle_root/compose.release.yaml"
  --profile runtime)
"${compose[@]}" config --quiet
if [[ "$start_server" -eq 1 ]]; then
  "${compose[@]}" up --no-build --wait
fi

echo "AutPlay server installer PASS"
echo "Admin Web is available only on loopback port 8787. Mobile ports: 18787/18788."
echo "Next: run server-control fingerprint and follow INSTALL_AND_PAIR.md."
