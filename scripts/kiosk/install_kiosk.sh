#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$script_dir/../.." && pwd)
multi_stream=false
no_start=false
source_mode=auto
cameras=auto
thor_config="$repo_dir/thor-demo.json"
feature_options=false
while (($#)); do
    case "$1" in
        --multi-stream) multi_stream=true ;;
        --no-start) no_start=true; feature_options=true ;;
        --source|--cameras|--config)
            (($# >= 2)) || { echo "$1 requires a value" >&2; exit 2; }
            case "$1" in
                --source) source_mode=$2 ;;
                --cameras) cameras=$2 ;;
                --config) thor_config=$2 ;;
            esac
            feature_options=true
            shift ;;
        --help|-h)
            echo 'Usage: scripts/kiosk/install_kiosk.sh [--multi-stream [--source auto|usb|store] [--cameras auto|2|4] [--config PATH] [--no-start]]'
            echo 'No options: install the upstream single-camera kiosk helpers for manual configuration.'
            echo '--multi-stream: prepare Thor models/software, configure the kiosk, and enable its services.'
            echo '--no-start: prepare and install only; do not enable or start services.'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
if [[ $EUID == 0 ]]; then
    echo 'Run as the desktop demo user, without sudo.' >&2
    exit 1
fi
if ! $multi_stream && $feature_options; then
    echo 'Camera/setup options require --multi-stream.' >&2
    exit 2
fi
case "$source_mode" in auto|usb|store) ;; *) echo 'Choose --source auto, usb, or store.' >&2; exit 2 ;; esac
case "$cameras" in auto|2|4) ;; *) echo 'Choose --cameras auto, 2, or 4.' >&2; exit 2 ;; esac

config_dir="$HOME/.config/live-vlm-webui"
lib_dir="$HOME/.local/lib/live-vlm-webui-kiosk"
unit_dir="$HOME/.config/systemd/user"
autostart_dir="$HOME/.config/autostart"

if $multi_stream; then
    # Complete preparation before changing desktop autostart/configuration or units.
    bash "$repo_dir/scripts/prepare_thor_host.sh" --with-browser
    bash "$repo_dir/scripts/prepare_usb_cameras.sh"
    thor_config=$(python3.12 -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$thor_config")
    python3.12 "$repo_dir/scripts/thor_demo.py" --config "$thor_config" kiosk-prepare
    if ! $no_start; then
        python3.12 "$script_dir/configure_multistream.py" --repo "$repo_dir" --config "$thor_config" check-start --env "$config_dir/kiosk.env"
    fi
fi

install -d -m 700 "$config_dir" "$lib_dir" "$unit_dir" "$autostart_dir"
if [[ ! -e $config_dir/kiosk.env ]]; then
    install -m 600 "$script_dir/kiosk.env.example" "$config_dir/kiosk.env"
fi
install -m 755 "$script_dir/start_kiosk_server.sh" "$script_dir/start_kiosk_browser.sh" "$lib_dir/"
install -m 644 "$script_dir/configure_kiosk_window.py" "$script_dir/configure_multistream.py" "$lib_dir/"
install -m 644 "$script_dir/"*.service "$unit_dir/"
if $multi_stream; then
    profile_changed=$(python3.12 "$script_dir/configure_multistream.py" --repo "$repo_dir" --config "$thor_config" \
        configure --env "$config_dir/kiosk.env" --source "$source_mode" --cameras "$cameras")
    if [[ $profile_changed != unchanged ]]; then
        # Keep pending handoffs across --no-start or headless preparation.
        touch "$config_dir/kiosk-profile.pending"
        for kind in server backend; do
            if [[ " $profile_changed " == *" $kind "* ]]; then
                touch "$config_dir/kiosk-$kind.pending"
            fi
        done
    fi
fi

# Expand the home directory in the shell; avoid nested desktop-entry escaping.
# Shell tilde expansion also preserves spaces in the home directory.
cat > "$autostart_dir/live-vlm-kiosk.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Live VLM kiosk
Exec=/bin/sh -c "exec ~/.local/lib/live-vlm-webui-kiosk/start_kiosk_browser.sh --session"
Terminal=false
EOF
systemctl --user daemon-reload
if ! $multi_stream; then
    echo "Installed kiosk launchers. Edit $config_dir/kiosk.env before starting."
    echo 'For a Python install: systemctl --user enable --now live-vlm-kiosk-server.service'
    echo 'For Docker: start the container with a restart policy instead (see docs/setup/kiosk.md).'
    echo "Then run: $lib_dir/start_kiosk_browser.sh --session"
    echo 'The browser will also start at the next desktop login.'
elif $no_start; then
    echo 'Multi-stream kiosk prepared and installed. Services were not enabled or started.'
    echo 'Run the same installer without --no-start when ready.'
else
    if python3.12 "$script_dir/configure_multistream.py" --repo "$repo_dir" --config "$thor_config" local-backend; then
        systemctl --user enable live-vlm-kiosk-cosmos.service
        backend_operation=start
        if [[ -e $config_dir/kiosk-backend.pending ]]; then backend_operation=restart; fi
        systemctl --user "$backend_operation" live-vlm-kiosk-cosmos.service
        rm -f -- "$config_dir/kiosk-backend.pending"
    else
        # Stop only this kiosk's unit. A reused backend was never its child, so
        # disabling the monitor leaves that pre-existing model server running.
        systemctl --user disable --now live-vlm-kiosk-cosmos.service
        rm -f -- "$config_dir/kiosk-backend.pending"
    fi
    systemctl --user enable live-vlm-kiosk-server.service
    server_operation=start
    if [[ -e $config_dir/kiosk-server.pending ]]; then server_operation=restart; fi
    systemctl --user "$server_operation" live-vlm-kiosk-server.service
    rm -f -- "$config_dir/kiosk-server.pending" "$config_dir/kiosk-server.previous.json"
    if [[ -n ${DISPLAY:-} || -n ${WAYLAND_DISPLAY:-} ]]; then
        if [[ -e $config_dir/kiosk-profile.pending ]]; then
            # Match upstream --session environment import, but restart directly
            # so an active kiosk receives the new preset without a second launch.
            session_vars=()
            for name in DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS; do
                if [[ -v $name ]]; then session_vars+=("$name"); fi
            done
            if ((${#session_vars[@]})); then
                systemctl --user import-environment "${session_vars[@]}"
            fi
            systemctl --user restart live-vlm-kiosk-browser.service
            rm -f -- "$config_dir/kiosk-profile.pending"
        else
            "$lib_dir/start_kiosk_browser.sh" --session
        fi
    else
        echo 'Services enabled. The kiosk browser will open at the next desktop login.'
    fi
    echo 'Multi-stream kiosk installed. Existing compatible services are reused.'
fi
