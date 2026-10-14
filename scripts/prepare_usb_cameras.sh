#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Install hot-plug camera nodes without accessing Docker or restarting services.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
helper_dir=/usr/local/libexec/live-vlm-webui
helper="$helper_dir/usb_camera_devices.py"
rule=/etc/udev/rules.d/99-live-vlm-cameras.rules
config=/etc/tmpfiles.d/live-vlm-cameras.conf
mirror=/dev/live-vlm-cameras
mode=install
case "${1:-}" in
    '') ;;
    --check) mode=check ;;
    --dry-run) mode=dry-run ;;
    --help|-h)
        echo 'Usage: scripts/prepare_usb_cameras.sh [--check|--dry-run]'
        echo 'Install root-owned USB hot-plug helpers; no cameras or app services are restarted.'
        exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
esac
(($# <= 1)) || { echo 'Choose one setup option.' >&2; exit 2; }

fail() { echo "USB camera setup: $*" >&2; exit 1; }

safe_directory() {
    local directory=$1 owner permissions
    [[ ! -L "$directory" ]] || fail "Refusing linked directory: $directory"
    [[ -d "$directory" ]] || fail "Missing directory: $directory"
    read -r owner permissions < <(stat -c '%u %a' -- "$directory")
    if [[ "$owner" != 0 ]] || (( (8#$permissions & 8#022) != 0 )); then
        fail "Directory must be root-owned and not writable by other users: $directory"
    fi
}

matches_file() {
    local source=$1 destination=$2 expected_mode=$3 owner permissions
    [[ -f "$destination" && ! -L "$destination" ]] || return 1
    read -r owner permissions < <(stat -c '%u %a' -- "$destination")
    [[ "$owner" == 0 && "$permissions" == "$expected_mode" ]] && cmp -s -- "$source" "$destination"
}

install_file() {
    local source=$1 destination=$2 permissions=$3
    [[ ! -L "$destination" ]] || fail "Refusing linked destination: $destination"
    [[ ! -e "$destination" || -f "$destination" ]] || fail "Not a regular file: $destination"
    if ! matches_file "$source" "$destination" "$permissions"; then
        install -o root -g root -m "$permissions" -- "$source" "$destination"
    fi
}

if [[ "$mode" == dry-run ]]; then
    echo "Install $helper (root:root 755)."
    echo "Install $rule and $config (root:root 644)."
    echo "Create stable $mirror (root:video 755), reload udev rules, and reconcile existing USB video nodes."
    echo 'Containers remain non-root; no privileged mode, Docker socket, camera resets or service restarts.'
    exit 0
fi

if [[ "$mode" == check ]]; then
    for directory in /usr/local /usr/local/libexec "$helper_dir" /etc/udev/rules.d /etc/tmpfiles.d "$mirror"; do
        safe_directory "$directory"
    done
    matches_file "$script_dir/usb_camera_devices.py" "$helper" 755 || fail 'Camera helper is missing or outdated.'
    matches_file "$script_dir/udev/99-live-vlm-cameras.rules" "$rule" 644 || fail 'Camera udev rule is missing or outdated.'
    matches_file "$script_dir/udev/live-vlm-cameras.conf" "$config" 644 || fail 'Camera boot directory configuration is missing or outdated.'
    video_gid=$(getent group video | cut -d: -f3)
    [[ -n "$video_gid" && "$(stat -c '%g %a' -- "$mirror")" == "$video_gid 755" ]] || \
        fail 'Camera directory must be root:video with mode 755.'
    echo 'USB camera hot-plug setup is installed.'
    exit 0
fi

if [[ "$EUID" != 0 ]]; then
    command -v sudo >/dev/null || fail 'Install sudo or run this host setup as root.'
    exec sudo -- bash "$script_dir/prepare_usb_cameras.sh"
fi
for command in python3 install stat cmp getent udevadm systemd-tmpfiles; do
    command -v "$command" >/dev/null || fail "Missing command: $command"
done
getent group video >/dev/null || fail 'The host video group is missing.'
# Validate each existing ancestor before writing privileged helper files.
for directory in /usr/local /usr/local/libexec "$helper_dir" /etc/udev /etc/udev/rules.d /etc/tmpfiles.d; do
    if [[ ! -e "$directory" && ! -L "$directory" ]]; then
        install -d -o root -g root -m 755 -- "$directory"
    fi
    safe_directory "$directory"
done
install_file "$script_dir/usb_camera_devices.py" "$helper" 755
install_file "$script_dir/udev/99-live-vlm-cameras.rules" "$rule" 644
install_file "$script_dir/udev/live-vlm-cameras.conf" "$config" 644
[[ ! -L "$mirror" ]] || fail "Refusing linked camera directory: $mirror"
if [[ -e "$mirror" ]]; then safe_directory "$mirror"; fi
systemd-tmpfiles --create "$config"
udevadm control --reload-rules
# Read current sysfs directly. Do not trigger/reset unrelated devices.
python3 "$helper"
echo 'USB camera hot-plug setup is ready. Existing app/model services were left running.'
