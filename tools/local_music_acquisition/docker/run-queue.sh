#!/bin/bash
# Generic operator launcher. Catalogs and playlists remain external to the image.
set -euo pipefail
umask 077
if (( $# < 3 )); then
    printf 'Usage: run-queue.sh PLAYLIST QUEUE_DIRECTORY MUSIC_DIRECTORY [acquisition options]\n' >&2
    exit 2
fi
playlist=$(realpath -e -- "$1")
queue=$(realpath -m -- "$2")
music=$(realpath -m -- "$3")
shift 3
: "${ACQUISITION_IMAGE:?Set the tested image ID or tag}"
: "${ACQUISITION_SECCOMP:?Set the Chromium seccomp profile path}"
cpus=${ACQUISITION_CPUS:-6}
workers=${ACQUISITION_WORKERS:-2}
index_recheck=${ACQUISITION_INDEX_RECHECK_SECONDS:-86400}
if [[ ! "$cpus" =~ ^([1-9]|[1-5][0-9]|6[0-4])$ || ! "$workers" =~ ^[1-4]$ ||
      ! "$index_recheck" =~ ^[0-9]{1,5}$ ]] || (( 10#$index_recheck > 86400 )); then
    printf 'Invalid acquisition CPU, worker or index setting\n' >&2
    exit 2
fi
for path in "$playlist" "$queue" "$music" "$ACQUISITION_SECCOMP"; do
    if [[ "$path" == *','* || "$path" == *$'\n'* ]]; then
        printf 'Unsupported mount path\n' >&2
        exit 2
    fi
done
test -f "$playlist"
test -f "$ACQUISITION_SECCOMP"
mkdir -p -- "$queue" "$music"
mounts=(--mount "type=bind,src=$playlist,dst=/input/playlist.txt,readonly"
        --mount "type=bind,src=$queue,dst=/queue"
        --mount "type=bind,src=$music,dst=/music")
options=(--output-dir /music --queue-dir /queue --normalize-numbered --workers "$workers"
         --index-recheck-seconds "$index_recheck")
container_options=()
if [[ -n "${ACQUISITION_CONTAINER_NAME:-}" ]]; then
    container_options+=(--name "$ACQUISITION_CONTAINER_NAME")
fi
for provider in HITMO YOUTUBE SOUNDCLOUD BANDCAMP; do
    variable="ACQUISITION_ENABLE_${provider}"
    default=1
    if [[ "$provider" == YOUTUBE ]]; then default=0; fi
    enabled=${!variable:-$default}
    if [[ "$enabled" != 0 && "$enabled" != 1 ]]; then
        printf 'Source enable settings must be 0 or 1\n' >&2
        exit 2
    fi
    flag=${provider,,}
    if [[ "$provider" == YOUTUBE ]]; then flag=yt-dlp; fi
    if [[ "$enabled" == 1 ]]; then
        options+=("--$flag-rights-confirmed")
        if [[ "$provider" == SOUNDCLOUD || "$provider" == BANDCAMP ]]; then
            options+=("--enable-$flag")
        fi
    elif [[ "$provider" == HITMO || "$provider" == YOUTUBE ]]; then
        options+=("--disable-$flag")
    fi
done
if [[ -n "${ACQUISITION_JAMENDO_ID:-}" ]]; then
    secret=$(realpath -e -- "$ACQUISITION_JAMENDO_ID")
    test -f "$secret"
    [[ "$secret" != *','* && "$secret" != *$'\n'* ]]
    mounts+=(--mount "type=bind,src=$secret,dst=/run/secrets/jamendo-client-id,readonly")
    options+=(--jamendo-client-id-file /run/secrets/jamendo-client-id)
else
    options+=(--disable-jamendo)
fi
for catalog_name in NORMALIZATION SOURCE; do
    variable="ACQUISITION_${catalog_name}_CATALOG"
    if [[ -n "${!variable:-}" ]]; then
        catalog=$(realpath -e -- "${!variable}")
        test -f "$catalog"
        [[ "$catalog" != *','* && "$catalog" != *$'\n'* ]]
        target="/catalog/${catalog_name,,}-catalog.json"
        mounts+=(--mount "type=bind,src=$catalog,dst=$target,readonly")
        options+=("--${catalog_name,,}-catalog" "$target")
    fi
done
if [[ -n "${ACQUISITION_SOUNDCLOUD_CLIENT_ID:-}" ]]; then
    client_id_file=$(realpath -e -- "$ACQUISITION_SOUNDCLOUD_CLIENT_ID")
    test -f "$client_id_file"
    [[ "$client_id_file" != *','* && "$client_id_file" != *$'\n'* ]]
    mounts+=(--mount "type=bind,src=$client_id_file,dst=/run/secrets/soundcloud-client-id,readonly")
    options+=(--soundcloud-client-id-file /run/secrets/soundcloud-client-id)
fi
if [[ -n "${ACQUISITION_PROXY_PROVIDERS:-}" ]]; then
    : "${ACQUISITION_XRAY_BINARY:?Set the existing Xray binary path}"
    : "${ACQUISITION_XRAY_CONFIG:?Set the existing Xray config path}"
    xray_binary=$(realpath -e -- "$ACQUISITION_XRAY_BINARY")
    xray_config=$(realpath -e -- "$ACQUISITION_XRAY_CONFIG")
    test -x "$xray_binary"
    test -f "$xray_config"
    for path in "$xray_binary" "$xray_config"; do
        [[ "$path" != *','* && "$path" != *$'\n'* ]]
    done
    mounts+=(--mount "type=bind,src=$xray_binary,dst=/opt/xray/xray,readonly"
             --mount "type=bind,src=$xray_config,dst=/run/secrets/xray-config.json,readonly")
    options+=(--xray-binary /opt/xray/xray --xray-config /run/secrets/xray-config.json
              --xray-proxy-url "${ACQUISITION_XRAY_PROXY_URL:-socks5h://127.0.0.1:10808}"
              --xray-startup-timeout "${ACQUISITION_XRAY_STARTUP_TIMEOUT:-15}"
              --xray-idle-timeout "${ACQUISITION_XRAY_IDLE_TIMEOUT:-60}"
              --xray-stop-timeout "${ACQUISITION_XRAY_STOP_TIMEOUT:-5}")
    IFS=',' read -ra proxy_providers <<< "$ACQUISITION_PROXY_PROVIDERS"
    for provider in "${proxy_providers[@]}"; do
        case "$provider" in
            yt_dlp|yt-dlp) options+=(--yt-dlp-requires-proxy) ;;
            soundcloud|bandcamp) options+=("--$provider-requires-proxy") ;;
            *) printf 'Unsupported proxy provider\n' >&2; exit 2 ;;
        esac
    done
    # Optional geo assets, kept separate from the configuration and never modified.
    if [[ -n "${ACQUISITION_XRAY_ASSETS:-}" ]]; then
        xray_assets=$(realpath -e -- "$ACQUISITION_XRAY_ASSETS")
        test -d "$xray_assets"
        [[ "$xray_assets" != *','* && "$xray_assets" != *$'\n'* ]]
        mounts+=(--mount "type=bind,src=$xray_assets,dst=/usr/local/share/xray,readonly")
        container_options+=(--env XRAY_LOCATION_ASSET=/usr/local/share/xray)
    fi
fi
exec docker run --rm --init \
    --user "${ACQUISITION_UID:-$(id -u)}:${ACQUISITION_GID:-$(id -g)}" \
    --read-only --tmpfs /tmp:size=1g,mode=1777 --cap-drop ALL --cap-add SYS_CHROOT \
    --security-opt no-new-privileges:true --security-opt "seccomp=$ACQUISITION_SECCOMP" \
    --pids-limit 256 --memory 2g --cpus "$cpus" --env HOME=/tmp \
    "${container_options[@]}" "${mounts[@]}" \
    "$ACQUISITION_IMAGE" /input/playlist.txt "${options[@]}" "$@"
