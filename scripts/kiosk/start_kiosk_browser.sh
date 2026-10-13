#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

# XDG autostart runs inside the actual desktop session (X11 or Wayland).
# Do not hard-code DISPLAY=:0 or start Chromium as root/from a boot service.
if [[ ${1:-} == --session ]]; then
    session_vars=()
    for name in DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS; do
        if [[ -v $name ]]; then session_vars+=("$name"); fi
    done
    if ((${#session_vars[@]})); then
        systemctl --user import-environment "${session_vars[@]}"
    fi
    exec systemctl --user start live-vlm-kiosk-browser.service
fi

# shellcheck source=/dev/null
source "$HOME/.config/live-vlm-webui/kiosk.env"
: "${KIOSK_PORT:?Set the server port}"
: "${KIOSK_BROWSER_PROFILE:?Set a dedicated browser profile}"
mode=${KIOSK_DISPLAY_MODE:-kiosk}
case "$mode" in
    full) mode=kiosk ;;
    kiosk|window) ;;
    *) echo 'KIOSK_DISPLAY_MODE must be kiosk, full, or window.' >&2; exit 1 ;;
esac

if [[ ${1:-} == --configure-window ]]; then
    exec python3 "$(dirname -- "${BASH_SOURCE[0]}")/configure_kiosk_window.py" \
        "$mode" "$KIOSK_BROWSER_PROFILE"
fi

browser=${KIOSK_BROWSER:-}
if [[ -z $browser ]]; then
    for candidate in chromium chromium-browser google-chrome google-chrome-stable; do
        if command -v "$candidate" >/dev/null 2>&1; then
            browser=$candidate
            break
        fi
    done
fi
if [[ -z $browser ]] || ! command -v "$browser" >/dev/null 2>&1; then
    echo 'Install Chromium/Chrome or set KIOSK_BROWSER in kiosk.env.' >&2
    exit 1
fi

url="http://localhost:${KIOSK_PORT}/"
launch_url="${url}?autostart=1"
case "${KIOSK_MODE:-single}" in
    single) ;;
    multi-stream)
        case "${KIOSK_SOURCE:-auto}" in auto|usb|store) ;; *) echo 'KIOSK_SOURCE must be auto, usb, or store.' >&2; exit 1 ;; esac
        case "${KIOSK_CAMERAS:-auto}" in auto|2|4) ;; *) echo 'KIOSK_CAMERAS must be auto, 2, or 4.' >&2; exit 1 ;; esac
        url="${url}multi-camera"
        launch_url="${url}?kiosk=1&demo=${KIOSK_SOURCE:-auto}&cameras=${KIOSK_CAMERAS:-auto}&autostart=1&temporal=1"
        ;;
    *) echo 'KIOSK_MODE must be single or multi-stream.' >&2; exit 1 ;;
esac

segmentation_ready() {
    [[ ${KIOSK_MODE:-single} == multi-stream ]] || return 0
    curl --noproxy '*' --fail --silent --max-time 5 "http://localhost:${KIOSK_PORT}/api/segmentation/status" | \
        python3 -c 'import json, sys; sys.exit(0 if json.load(sys.stdin).get("available") is True else 1)' 2>/dev/null
}
echo "Waiting for WebUI at $url and the configured VLM backend..."
until curl --noproxy '*' --fail --silent --output /dev/null --max-time 5 "$url" && \
    { [[ -z ${KIOSK_BACKEND_READY_URL:-} ]] || \
      curl --noproxy '*' --fail --silent --output /dev/null --max-time 5 "$KIOSK_BACKEND_READY_URL"; } && segmentation_ready; do
    sleep 2
done

mkdir -p "$KIOSK_BROWSER_PROFILE"
# The UI bypass selects the real default webcam; it does not generate fake video.
# Use this dedicated profile only for the demo (permission is auto-granted).
browser_args=(
    --user-data-dir="$KIOSK_BROWSER_PROFILE"
    --no-first-run
    --no-default-browser-check
    --disable-session-crashed-bubble
    --autoplay-policy=no-user-gesture-required
    --use-fake-ui-for-media-stream
)

if [[ $mode == kiosk ]]; then
    browser_args+=(--kiosk "$launch_url")
else
    # App mode keeps a normal title bar without tabs or an address bar.
    # X11's post-start helper fits it to the left half of the desktop work area.
    browser_args+=("--window-size=960,1080" "--window-position=0,0" "--app=$launch_url")
fi
exec "$browser" "${browser_args[@]}"
