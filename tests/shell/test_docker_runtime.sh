#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../scripts/docker_runtime.sh"

RED=""
GREEN=""
YELLOW=""
BLUE=""
NC=""
DOCKER_CMD="mock_docker"

mock_docker() {
    if [ "$1" = "info" ] && [ "$2" = "--format" ]; then
        printf '%s\n' "$MOCK_RUNTIMES"
        return 0
    fi
    return 1
}

assert_equals() {
    local expected="$1"
    local actual="$2"
    local description="$3"

    if [ "$expected" != "$actual" ]; then
        echo "FAIL: ${description}: expected '${expected}', got '${actual}'" >&2
        exit 1
    fi
}

test_runtime_detection() {
    MOCK_RUNTIMES='{"io.containerd.runc.v2":{},"nvidia":{"path":"nvidia-container-runtime"}}'
    docker_runtime_is_available nvidia
    ! docker_runtime_is_available missing
}

test_orin_uses_nvidia_runtime_when_registered() {
    MOCK_RUNTIMES='{"nvidia":{"path":"nvidia-container-runtime"},"runc":{"path":"runc"}}'
    PLATFORM="jetson-orin"
    GPU_FLAG=""
    RUNTIME_FLAG=""

    configure_jetson_gpu_access >/dev/null
    assert_equals "--runtime=nvidia" "$RUNTIME_FLAG" "Jetson Orin runtime flag"
    assert_equals "" "$GPU_FLAG" "Jetson Orin GPU request flag"
}

test_orin_stops_when_nvidia_runtime_is_missing() {
    MOCK_RUNTIMES='{"runc":{"path":"runc"}}'
    PLATFORM="jetson-orin"
    GPU_FLAG=""
    RUNTIME_FLAG=""

    if configure_jetson_gpu_access >/dev/null; then
        echo "FAIL: Jetson Orin accepted an unregistered NVIDIA runtime" >&2
        exit 1
    fi
    assert_equals "" "$RUNTIME_FLAG" "missing Orin runtime leaves no unsafe flag"
}

test_thor_behavior_is_unchanged() {
    PLATFORM="jetson-thor"
    GPU_FLAG=""
    RUNTIME_FLAG=""

    configure_jetson_gpu_access >/dev/null
    assert_equals "--runtime=nvidia" "$RUNTIME_FLAG" "Jetson Thor runtime flag"
    assert_equals "" "$GPU_FLAG" "Jetson Thor GPU request flag"
}

test_runtime_detection
test_orin_uses_nvidia_runtime_when_registered
test_orin_stops_when_nvidia_runtime_is_missing
test_thor_behavior_is_unchanged

echo "PASS: Docker runtime selection tests"
