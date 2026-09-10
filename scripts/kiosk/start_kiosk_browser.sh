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

source "$HOME/.config/live-vlm-webui/kiosk.env"
: "${KIOSK_PORT:?Set the server port}"
: "${KIOSK_BROWSER_PROFILE:?Set a dedicated browser profile}"

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
echo "Waiting for WebUI at $url and the configured VLM backend..."
until curl --noproxy '*' --fail --silent --output /dev/null --max-time 5 "$url" && \
    { [[ -z ${KIOSK_BACKEND_READY_URL:-} ]] || \
      curl --noproxy '*' --fail --silent --output /dev/null --max-time 5 "$KIOSK_BACKEND_READY_URL"; }; do
    sleep 2
done

mkdir -p "$KIOSK_BROWSER_PROFILE"
# The UI bypass selects the real default webcam; it does not generate fake video.
# Use this dedicated profile only for the demo (permission is auto-granted).
browser_args=(
    --user-data-dir="$KIOSK_BROWSER_PROFILE"
    --kiosk
    --no-first-run
    --no-default-browser-check
    --disable-session-crashed-bubble
    --autoplay-policy=no-user-gesture-required
    --use-fake-ui-for-media-stream
)

exec "$browser" "${browser_args[@]}" "${url}?autostart=1"
