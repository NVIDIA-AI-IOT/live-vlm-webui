#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

if [[ $EUID == 0 ]]; then
    echo 'Run as the desktop demo user, without sudo.' >&2
    exit 1
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
config_dir="$HOME/.config/live-vlm-webui"
lib_dir="$HOME/.local/lib/live-vlm-webui-kiosk"
unit_dir="$HOME/.config/systemd/user"
autostart_dir="$HOME/.config/autostart"

install -d -m 700 "$config_dir" "$lib_dir" "$unit_dir" "$autostart_dir"
if [[ ! -e $config_dir/kiosk.env ]]; then
    install -m 600 "$script_dir/kiosk.env.example" "$config_dir/kiosk.env"
fi
install -m 755 "$script_dir/start_kiosk_server.sh" "$script_dir/start_kiosk_browser.sh" "$lib_dir/"
install -m 644 "$script_dir/"*.service "$unit_dir/"

# The desktop-entry Exec field does not expand ~ or $HOME. A quoted shell handles it.
cat > "$autostart_dir/live-vlm-kiosk.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Live VLM kiosk
Exec=/bin/sh -c "exec \"\$HOME/.local/lib/live-vlm-webui-kiosk/start_kiosk_browser.sh\" --session"
Terminal=false
EOF
systemctl --user daemon-reload
echo "Installed kiosk launchers. Edit $config_dir/kiosk.env before starting."
echo 'For a Python install: systemctl --user enable --now live-vlm-kiosk-server.service'
echo 'For Docker: start the container with a restart policy instead (see docs/setup/kiosk.md).'
echo "Then run: $lib_dir/start_kiosk_browser.sh --session"
echo 'The browser will also start at the next desktop login.'
