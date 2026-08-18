#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Docker runtime helpers shared by container launchers.  These functions expect
# DOCKER_CMD and the colour variables from the calling script to be set.

docker_runtime_is_available() {
    local runtime_name="$1"
    local runtimes

    runtimes=$("$DOCKER_CMD" info --format '{{json .Runtimes}}' 2>/dev/null) || return 1
    printf '%s\n' "$runtimes" | grep -Eq "\\\"${runtime_name}\\\"[[:space:]]*:"
}

print_jetson_nvidia_runtime_repair_guidance() {
    echo -e "${RED}❌ Docker does not have the NVIDIA runtime registered.${NC}"
    echo -e "${YELLOW}   The Jetson Orin image needs the NVIDIA Container Toolkit; starting now"
    echo -e "   would fail with: unknown or invalid runtime name: nvidia.${NC}"
    echo ""
    echo -e "${BLUE}Repair the Docker runtime, then run this launcher again:${NC}"
    echo -e "${GREEN}sudo apt update${NC}"
    echo -e "${GREEN}sudo apt install -y nvidia-container-toolkit${NC}"
    echo -e "${GREEN}sudo nvidia-ctk runtime configure --runtime=docker${NC}"
    echo -e "${GREEN}sudo systemctl daemon-reload && sudo systemctl restart docker${NC}"
    echo ""
    echo -e "${BLUE}Verify that Docker now lists the runtime:${NC}"
    echo -e "${GREEN}docker info --format '{{json .Runtimes}}'${NC}"
    echo ""
    echo -e "${YELLOW}If 'nvidia' is still absent, reinstall the NVIDIA Container Toolkit from"
    echo -e "   the JetPack package repository before retrying.${NC}"
}

configure_jetson_gpu_access() {
    case "$PLATFORM" in
        jetson-orin)
            if docker_runtime_is_available "nvidia"; then
                RUNTIME_FLAG="--runtime=nvidia"
                echo -e "   ${GREEN}✓ NVIDIA Docker runtime detected${NC}"
            else
                print_jetson_nvidia_runtime_repair_guidance
                return 1
            fi
            ;;
        jetson-thor)
            # Preserve the existing launcher behavior for Thor. Issue #28 is
            # specific to Jetson Orin systems where the runtime is missing.
            RUNTIME_FLAG="--runtime=nvidia"
            ;;
    esac
}
