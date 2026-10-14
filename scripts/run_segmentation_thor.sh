#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build or run this checkout using a pinned, CUDA-enabled Jetson Thor image.
set -euo pipefail

# Emit Docker arguments for attached USB video devices. No camera is opened here;
# the application filters capture-capable nodes when listing available cameras.
# Roots are parameters so discovery can be exercised against an isolated fixture.
usb_camera_arguments() {
    local sysfs_root="${1:-/sys/class/video4linux}" device_root="${2:-/dev}"
    local entry name device_path ancestor gid usb
    local -A groups=()
    for entry in "${sysfs_root}"/video*; do
        name="${entry##*/}"
        [[ "${name}" =~ ^video[0-9]+$ ]] || continue
        device_path="${device_root}/${name}"
        [[ -c "${device_path}" ]] || continue
        ancestor="$(realpath -e -- "${entry}/device" 2>/dev/null)" || continue
        usb=false
        while [[ -n "${ancestor}" && "${ancestor}" != / ]]; do
            if [[ -r "${ancestor}/idVendor" && -r "${ancestor}/idProduct" ]]; then
                usb=true
                break
            fi
            ancestor="${ancestor%/*}"
        done
        ${usb} || continue
        gid="$(stat -Lc '%g' -- "${device_path}" 2>/dev/null)" || continue
        [[ "${gid}" =~ ^[0-9]+$ ]] || continue
        printf '%s\0' --device "${device_path}:/dev/${name}:rw"
        if [[ -z "${groups[${gid}]:-}" ]]; then
            printf '%s\0' --group-add "${gid}"
            groups["${gid}"]=1
        fi
    done
}


# A directory bind sees nodes added by udev after this container starts. The
# host helper owns this USB-only view; neither Docker nor the app creates nodes.
usb_hotplug_arguments() {
    local camera_root="${1:-/dev/live-vlm-cameras}" owner gid permissions
    [[ -d "${camera_root}" && ! -L "${camera_root}" ]] || return 1
    read -r owner gid permissions < <(stat -Lc '%u %g %a' -- "${camera_root}")
    [[ "${owner}" == 0 && "${gid}" =~ ^[0-9]+$ && "${permissions}" =~ ^[0-7]{3,4}$ ]] || return 1
    (( (8#${permissions} & 0022) == 0 )) || return 1
    printf '%s\0' --volume "${camera_root}:/dev/live-vlm-cameras:ro" \
        --device-cgroup-rule 'c 81:* rw' --group-add "${gid}" \
        --env LIVE_VLM_USB_DEVICE_ROOT=/dev/live-vlm-cameras
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/.." && pwd)"
image_name="${LIVE_VLM_SEGMENTATION_IMAGE:-live-vlm-webui:segmentation-thor}"
model_dir="$(realpath -m -- "${LIVE_VLM_MODEL_DIR:-${repo_dir}/.models}")"
state_dir="$(realpath -m -- "${LIVE_VLM_STATE_DIR:-${repo_dir}/artifacts/segmentation}")"
asset_base="https://github.com/ultralytics/assets/releases/download/v8.4.0"
mode=run
dry_run=false
skip_build=false
export_path=""
download_tmp=""
app_args=()
runtime_args=()
build_args=()

usage() {
    cat <<'HELP'
Usage: scripts/run_segmentation_thor.sh [runner options] [application options]

  --check               Check Docker/NVIDIA runtime, local image and model assets.
                        Read-only; no downloads, builds or containers started.
  --prepare             Download verified weights and build image, without starting.
  --export-engine PATH  Build the fixed retail TensorRT engine on this target GPU.
                        Remaining arguments are passed to the exporter.
  --skip-build          Reuse a prepared image (weights are still verified).
  --dry-run             Print the plan without Docker, downloads or writes.
  --runner-help         Show this help (--help normally belongs to the application).
  --                    Pass all remaining arguments directly to the application.

Environment (paths may be absolute or relative to the invoking directory):
  LIVE_VLM_MODEL_DIR                 Checkpoint directory (default: checkout/.models).
  LIVE_VLM_STATE_DIR                 Writable cache/config directory.
  LIVE_VLM_SEGMENTATION_IMAGE        Prepared application image tag.
  LIVE_VLM_SEGMENTATION_BASE_IMAGE   Override the Dockerfile's pinned GPU base.
  LIVE_VLM_SEGMENTATION_ENGINE       Target-built .engine; unset for PyTorch.
  LIVE_VLM_TRT_RUNTIME_PREFIX        Extracted TensorRT Debian package prefix.
  LIVE_VLM_TRT_CUDA_ROOT             Matching CUDA directory (set with TRT prefix).
  LIVE_VLM_TRT_IN_IMAGE=1            Use TRT/CUDA already installed in custom image.
  LIVE_VLM_DEMO_MAX_SIZE             Optional whole-frame recorded replay bound.

Retail export/run uses .models/thor-runtime prepared by:
  python3 scripts/prepare_segmentation_runtime.py
Explicit TRT/CUDA paths or TRT_IN_IMAGE=1 override this repository-private runtime.
The validated exporter uses TensorRT 10.16.2.10, CUDA 13.2 libraries, Python 3.12.
Default application binding is 127.0.0.1:8090; pass --host 0.0.0.0 for LAN access.
The turnkey installer enables USB cameras connected before or after launch.
For a standalone setup, enable hot-plug once with: bash scripts/prepare_usb_cameras.sh
Without that host setup, only USB devices attached at container creation are mapped.
HELP
}

fail() { printf '%s\n' "$*" >&2; exit 1; }
validate_mount_path() {
    case "$1" in
        *:*|*$'\n'*|*$'\r'*) fail 'Docker bind paths must not contain colons or line breaks.' ;;
    esac
}
set_mode() {
    [[ "${mode}" == run ]] || fail 'Choose only one of --check, --prepare and --export-engine.'
    mode="$1"
}
while (($#)); do
    case "$1" in
        --runner-help) usage; exit 0 ;;
        --check) set_mode check ;;
        --prepare) set_mode prepare ;;
        --export-engine)
            set_mode export
            (($# >= 2)) || fail '--export-engine requires an output path.'
            export_path="$(realpath -m -- "$2")"
            [[ "${export_path}" == *.engine ]] || fail 'Export output must end in .engine.'
            shift ;;
        --skip-build) skip_build=true ;;
        --dry-run) dry_run=true ;;
        --) shift; app_args+=("$@"); break ;;
        *) app_args+=("$1") ;;
    esac
    shift
done
if [[ "${mode}" == check || "${mode}" == prepare ]]; then
    ((${#app_args[@]} == 0)) || fail "${mode} does not accept application arguments."
fi

cleanup() { if [[ -n "${download_tmp}" ]]; then rm -f -- "${download_tmp}"; fi; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Native TensorRT engines are specific to their build target and runtime.
# Never guess a runtime under a previous machine's home directory.
for mount_path in "${repo_dir}" "${model_dir}" "${state_dir}" "${export_path}"; do
    validate_mount_path "${mount_path}"
done
engine_path=""
if [[ -n "${LIVE_VLM_SEGMENTATION_ENGINE:-}" ]]; then
    engine_path="$(realpath -m -- "${LIVE_VLM_SEGMENTATION_ENGINE}")"
    validate_mount_path "${engine_path}"
    [[ "${engine_path}" == *.engine ]] || fail 'LIVE_VLM_SEGMENTATION_ENGINE must end in .engine.'
fi
# The optional bootstrap creates this runtime solely from pinned public packages.
# A default PyTorch launch never needs or mounts it.
if [[ ( -n "${engine_path}" || "${mode}" == export ) &&
      -z "${LIVE_VLM_TRT_RUNTIME_PREFIX:-}" && -z "${LIVE_VLM_TRT_CUDA_ROOT:-}" &&
      "${LIVE_VLM_TRT_IN_IMAGE:-0}" != 1 ]]; then
    LIVE_VLM_TRT_RUNTIME_PREFIX="${repo_dir}/.models/thor-runtime"
    LIVE_VLM_TRT_CUDA_ROOT="${LIVE_VLM_TRT_RUNTIME_PREFIX}/usr/local/cuda-13.2"
    if ! ${dry_run}; then
        python3 "${script_dir}/prepare_segmentation_runtime.py" --check \
            --prefix "${LIVE_VLM_TRT_RUNTIME_PREFIX}" || \
            fail 'Prepare the retail runtime first: python3 scripts/prepare_segmentation_runtime.py'
    fi
fi
if [[ -n "${LIVE_VLM_TRT_RUNTIME_PREFIX:-}" || -n "${LIVE_VLM_TRT_CUDA_ROOT:-}" ]]; then
    [[ -n "${LIVE_VLM_TRT_RUNTIME_PREFIX:-}" && -n "${LIVE_VLM_TRT_CUDA_ROOT:-}" ]] || \
        fail 'Set both LIVE_VLM_TRT_RUNTIME_PREFIX and LIVE_VLM_TRT_CUDA_ROOT.'
    runtime_prefix="$(realpath -m -- "${LIVE_VLM_TRT_RUNTIME_PREFIX}")"
    cuda_root="$(realpath -m -- "${LIVE_VLM_TRT_CUDA_ROOT}")"
    validate_mount_path "${runtime_prefix}"
    validate_mount_path "${cuda_root}"
    if ! ${dry_run}; then
        for required_path in \
            "${runtime_prefix}/usr/lib/python3.12/dist-packages/tensorrt" \
            "${runtime_prefix}/usr/lib/aarch64-linux-gnu/libnvinfer.so.10" \
            "${runtime_prefix}/usr/lib/aarch64-linux-gnu/libnvinfer_plugin.so.10" \
            "${runtime_prefix}/usr/lib/aarch64-linux-gnu/libnvonnxparser.so.10" \
            "${cuda_root}/lib64/libcudart.so.13"; do
            [[ -e "${required_path}" ]] || fail "Missing TensorRT/CUDA runtime asset: ${required_path}"
        done
    fi
    runtime_args+=(
        --volume "${runtime_prefix}:/runtime:ro"
        --volume "${cuda_root}:/runtime-cuda:ro"
        --env PYTHONPATH=/runtime/usr/lib/python3.12/dist-packages
        --env LD_LIBRARY_PATH=/runtime/usr/lib/aarch64-linux-gnu:/runtime-cuda/lib64:/usr/local/nvidia/lib64:/usr/local/cuda/lib64
    )
elif [[ ( -n "${engine_path}" || "${mode}" == export ) && "${LIVE_VLM_TRT_IN_IMAGE:-0}" != 1 ]]; then
    fail 'Prepare the retail runtime first: python3 scripts/prepare_segmentation_runtime.py'
fi
if [[ -n "${engine_path}" && "${mode}" != export ]]; then
    if ! ${dry_run} && [[ "${mode}" != prepare ]]; then
        [[ -f "${engine_path}" ]] || fail "Missing target-built retail engine: ${engine_path}"
    fi
    runtime_args+=(--volume "${engine_path}:/app/.models/retail-480.engine:ro"
                  --env LIVE_VLM_SEGMENTATION_MODEL=/app/.models/retail-480.engine)
fi
if [[ -n "${LIVE_VLM_DEMO_MAX_SIZE:-}" ]]; then
    runtime_args+=(--env "LIVE_VLM_DEMO_MAX_SIZE=${LIVE_VLM_DEMO_MAX_SIZE}")
fi
if [[ -n "${LIVE_VLM_SEGMENTATION_BASE_IMAGE:-}" ]]; then
    build_args+=(--build-arg "SEGMENTATION_BASE_IMAGE=${LIVE_VLM_SEGMENTATION_BASE_IMAGE}")
fi

# The host-owned USB view stays present even when no cameras are attached.
# Legacy standalone launches retain their existing per-device mappings until
# host hot-plug support is installed; the turnkey installer always installs it.
if [[ "${mode}" == run ]]; then
    if [[ -e /dev/live-vlm-cameras || -L /dev/live-vlm-cameras ]]; then
        usb_hotplug_arguments /dev/live-vlm-cameras >/dev/null || \
            fail 'Invalid USB camera directory; run bash scripts/prepare_usb_cameras.sh.'
        while IFS= read -r -d '' camera_argument; do
            runtime_args+=("${camera_argument}")
        done < <(usb_hotplug_arguments /dev/live-vlm-cameras)
    else
        while IFS= read -r -d '' camera_argument; do
            runtime_args+=("${camera_argument}")
        done < <(usb_camera_arguments /sys/class/video4linux /dev)
    fi
fi

# Elevate Docker alone if necessary, leaving downloaded/generated files user-owned.
docker_command=(docker)
if ! ${dry_run}; then
    for command_name in docker curl sha256sum mktemp realpath; do
        command -v "${command_name}" >/dev/null 2>&1 || fail "Required command is missing: ${command_name}"
    done
    if docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
        :
    elif command -v sudo >/dev/null 2>&1 && sudo -n docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
        docker_command=(sudo -n docker)
    else
        fail 'Cannot access Docker. Enable Docker access for this user, then retry.'
    fi
    "${docker_command[@]}" info --format '{{json .Runtimes}}' | grep -q '"nvidia"' || \
        fail 'Docker NVIDIA runtime is unavailable. Install/configure NVIDIA Container Toolkit for this Jetson.'
    [[ "$(uname -m)" == aarch64 ]] || fail 'This runner targets aarch64 Jetson Thor.'
fi

print_command() { printf '%q ' "$@"; printf '\n'; }
build_command=("${docker_command[@]}" build --file "${repo_dir}/docker/Dockerfile.segmentation-thor"
               --tag "${image_name}" "${build_args[@]}" "${repo_dir}")
run_command=("${docker_command[@]}" run --rm --init --runtime=nvidia --gpus all --network host
    --user "$(id -u):$(id -g)"
    --volume "${repo_dir}:/app"
    --volume "${model_dir}:/app/.models:ro"
    --volume "${state_dir}:/state"
    --env PYTHONDONTWRITEBYTECODE=1
    --env XDG_CONFIG_HOME=/state/config
    --env XDG_CACHE_HOME=/state/cache
    --env YOLO_CONFIG_DIR=/state/config/ultralytics
    --env MPLCONFIGDIR=/state/cache/matplotlib
    --env TORCH_HOME=/state/cache/torch
    --env TORCHINDUCTOR_CACHE_DIR=/state/cache/torchinductor
    --env USER="$(id -un)"
    "${runtime_args[@]}")
if [[ "${mode}" == run ]]; then
    if [[ -n "${LIVE_VLM_CONTAINER_NAME:-}" ]]; then
        [[ "${LIVE_VLM_CONTAINER_NAME}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || fail 'Invalid application container name.'
        run_command+=(--name "${LIVE_VLM_CONTAINER_NAME}")
    fi
    if [[ -n "${LIVE_VLM_CONTAINER_CIDFILE:-}" ]]; then
        run_command+=(--cidfile "${LIVE_VLM_CONTAINER_CIDFILE}")
    fi
fi
if [[ "${mode}" == export ]]; then
    run_command+=(--volume "$(dirname -- "${export_path}"):/export"
                  --entrypoint python "${image_name}"
                  /app/scripts/export_retail_segmentation.py
                  --weights /app/.models/yoloe-26s-seg.pt
                  --output "/export/$(basename -- "${export_path}")" "${app_args[@]}")
else
    run_command+=("${image_name}" --host 127.0.0.1 --port 8090 "${app_args[@]}")
fi
if ${dry_run}; then
    printf 'Mode: %s; model assets: %s; writable state: %s\n' "${mode}" "${model_dir}" "${state_dir}"
    if [[ "${mode}" == check ]]; then
        printf 'Would check Docker/NVIDIA runtime, image, runtime paths and asset hashes; no changes.\n'
    else
        printf 'Would verify/download pinned YOLOE checkpoint and encoder if needed.\n'
        if ! ${skip_build}; then print_command "${build_command[@]}"; fi
        if [[ "${mode}" != prepare ]]; then print_command "${run_command[@]}"; fi
    fi
    exit 0
fi

download_asset() {
    local filename="$1" digest="$2" destination="${model_dir}/$1"
    if [[ -e "${destination}" ]]; then
        printf '%s  %s\n' "${digest}" "${destination}" | sha256sum --check --status || \
            fail "Checksum mismatch for ${destination}; existing file was left unchanged."
        printf 'Verified model asset: %s\n' "${filename}"
        return
    fi
    [[ "${mode}" != check ]] || fail "Missing model asset: ${destination}; run --prepare first."
    download_tmp="$(mktemp "${model_dir}/.${filename}.download.XXXXXX")"
    printf 'Downloading official model asset: %s\n' "${filename}"
    curl --fail --location --show-error --retry 3 --connect-timeout 15 --max-time 900 \
        --proto '=https' --tlsv1.2 --output "${download_tmp}" "${asset_base}/${filename}"
    printf '%s  %s\n' "${digest}" "${download_tmp}" | sha256sum --check --status || \
        fail "Downloaded asset checksum mismatch: ${filename}"
    mv -- "${download_tmp}" "${destination}"
    download_tmp=""
}

if [[ "${mode}" == check ]] || ${skip_build}; then
    "${docker_command[@]}" image inspect "${image_name}" >/dev/null 2>&1 || \
        fail "Missing prepared image: ${image_name}; run --prepare first."
fi
if [[ "${mode}" != check ]]; then
    mkdir -p -- "${model_dir}" "${state_dir}/config/ultralytics" "${state_dir}/cache"
fi
# Engine inference does not need the training checkpoint/text encoder; export does.
if [[ -z "${engine_path}" || "${mode}" == export || "${mode}" == prepare ]]; then
    download_asset yoloe-26s-seg.pt 48f24206bc8680d60cbbfa296b0140da849669b9515058b72f5a945142df0654
    download_asset mobileclip2_b.ts 35d7f213e4d75f38514e4656ad3cb91158bd33e3805d8ac349f23b186f66982f
fi
if [[ "${mode}" == check ]]; then
    printf 'Local prerequisites present. GPU inference and engine compatibility still require a target smoke test.\n'
    exit 0
fi
if ! ${skip_build}; then "${build_command[@]}"; fi
if [[ "${mode}" == prepare ]]; then
    printf 'Prepared %s. No application container was started.\n' "${image_name}"
    exit 0
fi
if [[ "${mode}" == export ]]; then
    mkdir -p -- "$(dirname -- "${export_path}")"
    printf 'Exporting the retail engine on this GPU; the running web UI is unchanged.\n'
else
    printf 'Starting this checkout. Open /multi-camera on the configured HTTPS address.\n'
fi
exec "${run_command[@]}"
