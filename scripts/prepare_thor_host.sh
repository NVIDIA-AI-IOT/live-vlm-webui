#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Prepare host tools on the validated Thor image; GPU Python libraries stay in containers.
# NVIDIA runtime procedure:
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#configuring-docker
set -euo pipefail

mode=install
with_browser=false
for argument in "$@"; do
    case "${argument}" in
        --with-browser) with_browser=true ;;
        --check|--dry-run)
            [[ "${mode}" == install ]] || { printf '%s\n' 'Choose either --check or --dry-run.' >&2; exit 2; }
            mode="${argument#--}" ;;
        --help|-h)
            printf '%s\n' 'Usage: bash scripts/prepare_thor_host.sh [--check | --dry-run] [--with-browser]' \
                'Install missing host tools and configure local Docker for Jetson Thor T5000 / L4T 39.2.1.' \
                '--check is read-only. --dry-run prints necessary changes without applying them.' \
                'JetPack, GPU drivers and running containers are never upgraded or stopped.'
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "${argument}" >&2; exit 2 ;;
    esac
done
fail() { printf 'Host setup: %s\n' "$*" >&2; exit 1; }
show() { printf '  '; printf '%q ' "$@"; printf '\n'; }

[[ "${BASH_VERSINFO[0]}" -ge 4 ]] || fail 'Bash 4 or newer is required.'
[[ "$(uname -m)" == aarch64 ]] || fail 'This setup targets ARM64 Jetson Thor T5000.'
[[ -r /etc/os-release ]] || fail 'Cannot identify Ubuntu.'
# shellcheck source=/dev/null
source /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || fail 'Use the validated Ubuntu 24.04 JetPack image.'
[[ -r /proc/device-tree/model && -r /etc/nv_tegra_release && -r /etc/nv_boot_control.conf ]] || \
    fail 'Jetson hardware/release information is missing.'
model="$(tr -d '\000' < /proc/device-tree/model)"
boot="$(tr '[:upper:]' '[:lower:]' < /etc/nv_boot_control.conf)"
[[ "${model,,}" == *thor* ]] || fail 'This setup requires Jetson Thor.'
grep -q '^# R39 (release), REVISION: 2\.1,' /etc/nv_tegra_release || \
    fail 'This demo is validated for L4T 39.2.1; install that JetPack image separately.'
[[ "${boot}" != *t4000* && ( "${boot}" == *t5000* || "${boot}" == *jetson-agx-thor-devkit* ) ]] || \
    fail 'This demo is validated for Thor T5000; the detected module is different or unknown.'

admin=()
admin_ready=false
ensure_admin() {
    ${admin_ready} && return
    if [[ "${EUID}" -eq 0 ]]; then
        admin_ready=true
    elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        admin=(sudo -n)
        admin_ready=true
    elif [[ "${mode}" == install && -t 0 && -t 2 ]] && command -v sudo >/dev/null 2>&1; then
        printf '%s\n' 'Administrator access is needed for the missing host prerequisites.'
        sudo -v || fail 'Administrator authentication failed.'
        admin=(sudo)
        admin_ready=true
    else
        fail 'Administrator access is required. Run this script from a terminal with sudo access; noninteractive setup needs passwordless sudo.'
    fi
}

# Match the local rootful Docker socket that the repository runners use. Do not
# inspect a remote daemon and then accidentally reconfigure this Jetson's daemon.
assert_local_docker() {
    local endpoint
    endpoint="${DOCKER_HOST:-}"
    if [[ -z "${endpoint}" ]]; then
        endpoint="$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null)" || \
            fail 'Cannot inspect the Docker context. Select the local default Docker context and retry.'
    fi
    case "${endpoint}" in
        unix:///var/run/docker.sock|unix:///run/docker.sock) ;;
        *) fail 'Select the local rootful Docker context before setup (docker context use default; unset DOCKER_HOST DOCKER_CONTEXT).' ;;
    esac
}

docker_command=()
docker_ready=false
probe_docker() {
    docker_ready=false
    docker_command=()
    command -v docker >/dev/null 2>&1 || return 0
    assert_local_docker
    if docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
        docker_command=(docker)
        docker_ready=true
    elif [[ "${EUID}" -ne 0 ]] && command -v sudo >/dev/null 2>&1 && \
        sudo -n docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
        docker_command=(sudo -n docker)
        docker_ready=true
    fi
}
nvidia_ready() {
    ${docker_ready} && [[ "$("${docker_command[@]}" info --format '{{if index .Runtimes "nvidia"}}yes{{end}}')" == yes ]]
}
require_idle_docker() {
    local running
    running="$("${docker_command[@]}" ps -q)" || fail 'Cannot check running containers; Docker configuration was not changed.'
    [[ -z "${running}" ]] || fail 'Docker has running containers and its NVIDIA runtime is not configured. Stop those workloads yourself, then rerun this script. No containers were stopped.'
}

packages=()
add_package() {
    local package
    for package in "${packages[@]}"; do [[ "${package}" == "$1" ]] && return 0; done
    packages+=("$1")
}
command -v python3 >/dev/null 2>&1 || add_package python3
command -v python3.12 >/dev/null 2>&1 || add_package python3.12
if ! command -v python3.12 >/dev/null 2>&1 || \
    ! python3.12 -c 'import sys, venv, ensurepip; assert sys.version_info[:2] == (3, 12)' >/dev/null 2>&1; then
    add_package python3-venv
fi
for pair in 'bash:bash' 'curl:curl' 'git:git' 'dpkg-deb:dpkg'; do
    command -v "${pair%%:*}" >/dev/null 2>&1 || add_package "${pair#*:}"
done
if ${with_browser}; then
    browser_ready=false
    for candidate in "${KIOSK_BROWSER:-}" chromium chromium-browser google-chrome google-chrome-stable; do
        if [[ -n "${candidate}" ]] && command -v "${candidate}" >/dev/null 2>&1; then
            browser_ready=true
            break
        fi
    done
    ${browser_ready} || add_package chromium-browser
    # The kiosk's X11 helper corrects fullscreen state and positions app windows.
    for pair in 'wmctrl:wmctrl' 'xprop:x11-utils'; do
        command -v "${pair%%:*}" >/dev/null 2>&1 || add_package "${pair#*:}"
    done
fi
had_docker=false
if command -v docker >/dev/null 2>&1; then
    had_docker=true
else
    add_package docker.io
fi
probe_docker
if ${docker_ready} && ! nvidia_ready; then
    # Catch occupied daemons before installing anything that might alter Docker.
    require_idle_docker
fi
need_toolkit=false
if ! nvidia_ready && ! command -v nvidia-ctk >/dev/null 2>&1; then
    need_toolkit=true
fi

if [[ "${mode}" == check ]]; then
    ((${#packages[@]} == 0)) || fail "Missing host prerequisites: ${packages[*]}. Run ./scripts/kiosk/install_kiosk.sh --multi-stream to install them."
    ${docker_ready} || fail 'Local Docker is not reachable. Start Docker or enable sudo/Docker access, then retry.'
    nvidia_ready || fail 'Docker NVIDIA runtime is missing. Run ./scripts/kiosk/install_kiosk.sh --multi-stream to configure it once Docker has no running containers.'
    printf '%s\n' 'Host ready: Thor T5000, Python 3.12 with venv, Docker NVIDIA runtime.'
    exit 0
fi

# Use only the toolkit revision already offered by the configured JetPack apt
# repository. No new apt sources, keys, JetPack packages or drivers are added.
toolkit_packages() {
    local version package
    version="$(apt-cache madison nvidia-container-toolkit | awk -F '|' \
        '$3 ~ /https:\/\/repo.download.nvidia.com\/jetson\/common[[:space:]]+r39\.2\// { gsub(/[[:space:]]/, "", $2); print $2; exit }')"
    [[ -n "${version}" ]] || fail 'The JetPack r39.2 apt repository does not offer nvidia-container-toolkit. Restore the JetPack apt sources and retry; this script does not add a third-party repository.'
    for package in nvidia-container-toolkit nvidia-container-toolkit-base libnvidia-container-tools libnvidia-container1; do
        apt-cache madison "${package}" | awk -F '|' -v wanted="${version}" \
            '$3 ~ /https:\/\/repo.download.nvidia.com\/jetson\/common[[:space:]]+r39\.2\// { gsub(/[[:space:]]/, "", $2); if ($2 == wanted) found=1 } END { exit !found }' || \
            fail "JetPack apt repository is missing matching ${package}=${version}."
        add_package "${package}=${version}"
    done
}

if [[ "${mode}" == dry-run ]]; then
    if ((${#packages[@]})) || ${need_toolkit}; then
        printf '%s\n' 'Would install missing host prerequisites from the configured Ubuntu/JetPack apt repositories:'
        show sudo env NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get update
        if ${need_toolkit}; then toolkit_packages; fi
        show sudo env NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-upgrade --no-remove "${packages[@]}"
    fi
    if ! ${docker_ready}; then
        printf '%s\n' 'Would start the local Docker service if stopped, then verify access and NVIDIA runtime.'
        show sudo systemctl start docker
    fi
    if ! nvidia_ready; then
        printf '%s\n' 'Only if Docker has no running containers and still lacks NVIDIA runtime:'
        show sudo nvidia-ctk runtime configure --runtime=docker
        show sudo systemctl restart docker
    fi
    printf '%s\n' 'Host preview complete. No packages, daemon settings or services were changed.'
    exit 0
fi

# A readable running daemon may only require administrator authentication. Check
# that before starting a daemon or installing a toolkit unnecessarily.
if ${had_docker} && ! ${docker_ready}; then
    ensure_admin
    if "${admin[@]}" docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
        docker_command=("${admin[@]}" docker)
        docker_ready=true
        if nvidia_ready; then need_toolkit=false; else require_idle_docker; fi
    fi
fi
if ((${#packages[@]})) || ${need_toolkit}; then
    ensure_admin
    printf '%s\n' 'Installing missing host prerequisites…'
    "${admin[@]}" env NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get update
    if ${need_toolkit}; then toolkit_packages; fi
    # Refuse dependency solutions that would upgrade JetPack/drivers or replace
    # an existing Docker installation. --no-remove also prevents removals.
    plan="$("${admin[@]}" env NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive \
        apt-get -s install -y --no-install-recommends --no-upgrade --no-remove "${packages[@]}")" || \
        fail 'Apt could not resolve the missing prerequisites.'
    while read -r action package _; do
        [[ "${action}" == Inst ]] || continue
        case "${package}" in
            nvidia-l4t-*|nvidia-jetpack*|nvidia-driver-*|nvidia-open*|cuda-drivers*|linux-image-*|linux-modules-*)
                fail "Apt would change ${package}; repair the JetPack package state separately." ;;
            docker*|containerd*|runc)
                if ${had_docker}; then fail "Apt would change ${package} in an existing Docker installation; install host prerequisites separately."; fi ;;
        esac
    done <<< "${plan}"
    "${admin[@]}" env NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive \
        apt-get install -y --no-install-recommends --no-upgrade --no-remove "${packages[@]}"
    # The Docker package may already have started its new daemon.
    probe_docker
fi
if ! ${docker_ready}; then
    ensure_admin
    command -v systemctl >/dev/null 2>&1 || fail 'Systemd is required to start local Docker.'
    if "${admin[@]}" systemctl is-active --quiet docker; then
        fail 'Docker is active but unreachable. Check Docker access/configuration; this script will not restart it blindly.'
    fi
    printf '%s\n' 'Starting Docker…'
    "${admin[@]}" systemctl start docker
    "${admin[@]}" docker info --format '{{.ServerVersion}}' >/dev/null || fail 'Docker did not become ready.'
    docker_command=("${admin[@]}" docker)
    docker_ready=true
fi
if ! nvidia_ready; then
    ensure_admin
    require_idle_docker
    command -v nvidia-ctk >/dev/null 2>&1 || fail 'NVIDIA Container Toolkit is unavailable after installation.'
    printf '%s\n' 'Enabling the NVIDIA runtime for Docker…'
    "${admin[@]}" nvidia-ctk runtime configure --runtime=docker
    # Recheck immediately before restart; a concurrently started workload must
    # not be stopped merely to finish demo setup.
    require_idle_docker
    "${admin[@]}" systemctl restart docker
    nvidia_ready || fail 'Docker did not report the NVIDIA runtime after configuration.'
fi
python3.12 -c 'import sys, venv, ensurepip; assert sys.version_info[:2] == (3, 12)' || \
    fail 'Python 3.12 with venv/ensurepip is still unavailable; check any custom Python installation.'
printf '%s\n' 'Host ready: Thor T5000, Python 3.12 with venv, Docker NVIDIA runtime.'
