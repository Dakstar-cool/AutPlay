#!/bin/sh
set -eu
umask 077

browser_needed=1
for argument in "$@"; do
    case "$argument" in
        queue|--disable-hitmo|--check-runtime|--help) browser_needed=0 ;;
    esac
done
browser_pid=""
worker_pid=""
stopping=0
cleanup() {
    if [ -n "$browser_pid" ]; then
        kill "$browser_pid" 2>/dev/null || true
        wait "$browser_pid" 2>/dev/null || true
    fi
}
stop_worker() {
    stopping=1
    if [ -n "$worker_pid" ]; then kill -TERM "$worker_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap stop_worker TERM INT
if [ "$browser_needed" = 1 ]; then
    profile_root=$(mktemp -d /tmp/autplay-browser.XXXXXX)
    chromium --headless=new --disable-gpu --disable-dev-shm-usage \
        --no-first-run --no-default-browser-check --disable-background-networking \
        --disable-component-update --disable-sync --metrics-recording-only \
        --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
        --user-data-dir="$profile_root" about:blank >/tmp/autplay-browser.log 2>&1 &
    browser_pid=$!
    ready=0
    for attempt in $(seq 1 40); do
        if [ "$stopping" = 1 ]; then exit 0; fi
        if curl --silent --fail --max-time 1 http://127.0.0.1:9222/json/version >/dev/null; then
            ready=1
            break
        fi
        kill -0 "$browser_pid" 2>/dev/null || break
        sleep 0.25
    done
    if [ "$ready" != 1 ]; then
        printf '{"error":"chromium_cdp_unavailable"}\n' >&2
        exit 2
    fi
fi
if [ "$stopping" = 1 ]; then exit 0; fi
/opt/acquisition/.venv/bin/local-music-acquire "$@" &
worker_pid=$!
if [ "$stopping" = 1 ]; then stop_worker; fi
result=0
wait "$worker_pid" || result=$?
# A signal can interrupt wait before the worker has saved its in-flight receipt.
if kill -0 "$worker_pid" 2>/dev/null; then
    result=0
    wait "$worker_pid" || result=$?
fi
exit "$result"
