#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Auto-detect platform and start the appropriate Live-VLM-WebUI Docker container

set -e

# ==============================================================================
# Parse command-line arguments
# ==============================================================================
REQUESTED_VERSION=""
LIST_VERSIONS=false
SKIP_VERSION_CHECK=false
SIMULATE_PUBLIC=false

show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --version VERSION      Specify Docker image version (e.g., 0.2.0, latest)"
    echo "  --list-versions        List available Docker image versions and exit"
    echo "  --skip-version-pick    Skip interactive version selection (use latest)"
    echo "  --simulate-public      Simulate public API access (ignore GITHUB_TOKEN)"
    echo "  -h, --help            Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                          # Interactive mode - choose version"
    echo "  $0 --version 0.2.0          # Use specific version"
    echo "  $0 --version latest         # Use latest version"
    echo "  $0 --skip-version-pick      # Use latest without prompting"
    echo "  $0 --list-versions          # List available versions"
    echo "  $0 --list-versions --simulate-public  # Test public API (no token)"
    echo ""
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --version)
            REQUESTED_VERSION="$2"
            shift 2
            ;;
        --list-versions)
            LIST_VERSIONS=true
            shift
            ;;
        --skip-version-pick)
            SKIP_VERSION_CHECK=true
            shift
            ;;
        --simulate-public)
            SIMULATE_PUBLIC=true
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo ""
            show_usage
            exit 1
            ;;
    esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==============================================================================
# Functions to fetch and display available versions
# ==============================================================================

# Fetch available versions from GitHub Container Registry (requires auth)
fetch_versions_from_ghcr() {
    local repo_owner="nvidia-ai-iot"
    local repo_name="live-vlm-webui"

    # Use GitHub Packages API (requires read:packages scope)
    local api_url="https://api.github.com/orgs/${repo_owner}/packages/container/${repo_name}/versions"

    if command -v curl &> /dev/null && command -v jq &> /dev/null; then
        local response=$(curl -s -H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github.v3+json" "${api_url}" 2>/dev/null)

        # Check if successful
        if ! echo "$response" | grep -q '"message"'; then
            # Extract tags from metadata
            echo "$response" | jq -r '.[].metadata.container.tags[]?' 2>/dev/null | grep -v '^null$' | sort -V -r | uniq
            return 0
        fi
    fi
    return 1
}

# Fetch available versions from GitHub Releases (public API, no auth required)
fetch_versions_from_releases() {
    local repo_owner="nvidia-ai-iot"
    local repo_name="live-vlm-webui"

    # Use GitHub Releases API (public, rate-limited but no auth needed)
    # Rate limits: 60/hour without auth, 5000/hour with GITHUB_TOKEN
    local api_url="https://api.github.com/repos/${repo_owner}/${repo_name}/releases"

    if command -v curl &> /dev/null; then
        local response=""

        # Use GITHUB_TOKEN if available for higher rate limits (but don't require it)
        if [ -n "$GITHUB_TOKEN" ] && [ "$SIMULATE_PUBLIC" != "true" ]; then
            response=$(curl -s -H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github.v3+json" "${api_url}" 2>/dev/null)
        else
            # Public API - works without auth (60 requests/hour limit)
            response=$(curl -s -H "Accept: application/vnd.github.v3+json" "${api_url}" 2>/dev/null)
        fi

        # Check if we hit rate limit
        if echo "$response" | grep -q '"message.*rate limit"'; then
            return 1
        fi

        if [ -n "$response" ]; then
            if command -v jq &> /dev/null; then
                # Parse with jq - extract tag_name and remove 'v' prefix
                echo "$response" | jq -r '.[].tag_name' 2>/dev/null | sed 's/^v//' | sort -V -r | uniq
            else
                # Parse manually without jq
                echo "$response" | grep -o '"tag_name":"[^"]*"' | cut -d'"' -f4 | sed 's/^v//' | sort -V -r | uniq
            fi
        fi
    fi
}

# Hybrid version fetcher: Try GHCR first, fall back to Releases API
fetch_available_versions() {
    local versions=""
    local source=""

    # Try GHCR API first if GITHUB_TOKEN is available (and not simulating public)
    if [ -n "$GITHUB_TOKEN" ] && [ "$SIMULATE_PUBLIC" != "true" ]; then
        versions=$(fetch_versions_from_ghcr)
        if [ -n "$versions" ]; then
            source="ghcr"
            echo "$versions"
            return 0
        fi
    fi

    # Fall back to public Releases API
    versions=$(fetch_versions_from_releases)
    if [ -n "$versions" ]; then
        source="releases"
        echo "$versions"
        return 0
    fi

    # Both failed
    return 1
}

# List available versions
list_versions() {
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}    Available Docker Image Versions${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo ""

    # Show what mode we're in
    if [ "$SIMULATE_PUBLIC" = "true" ]; then
        echo -e "${YELLOW}🧪 Simulating public API access (GITHUB_TOKEN ignored)${NC}"
        echo ""
    fi

    echo -e "${YELLOW}📦 Fetching available versions...${NC}"

    # Try GHCR first
    local versions=""
    local source=""

    if [ -n "$GITHUB_TOKEN" ] && [ "$SIMULATE_PUBLIC" != "true" ]; then
        echo -e "${BLUE}   Trying GitHub Container Registry (authenticated)...${NC}"
        versions=$(fetch_versions_from_ghcr)
        if [ -n "$versions" ]; then
            source="ghcr"
            echo -e "${GREEN}   ✓ Successfully fetched from GHCR${NC}"
        else
            echo -e "${YELLOW}   ✗ GHCR failed, falling back to Releases API...${NC}"
        fi
    fi

    # Fall back to Releases API
    if [ -z "$versions" ]; then
        echo -e "${BLUE}   Trying GitHub Releases (public API)...${NC}"
        versions=$(fetch_versions_from_releases)
        if [ -n "$versions" ]; then
            source="releases"
            echo -e "${GREEN}   ✓ Successfully fetched from Releases${NC}"
        fi
    fi

    echo ""

    if [ -z "$versions" ]; then
        echo -e "${YELLOW}⚠️  Could not fetch versions from GitHub${NC}"
        echo -e "${YELLOW}   (Rate limit reached or network issue)${NC}"
        echo ""
        if [ -z "$GITHUB_TOKEN" ]; then
            echo -e "${BLUE}💡 Tip: Set GITHUB_TOKEN for higher rate limits:${NC}"
            echo -e "   ${GREEN}export GITHUB_TOKEN=ghp_xxxxxxxxxxxxx${NC}"
            echo -e "   Rate limits: 60/hour → 5000/hour"
            echo ""
        fi
        echo -e "${YELLOW}   Common versions:${NC}"
        echo -e "   - ${GREEN}latest${NC} (most recent release)"
        echo -e "   - ${GREEN}0.1.1${NC}"
        echo -e "   - ${GREEN}0.1.0${NC}"
        echo ""
        echo -e "${BLUE}ℹ️  Platform-specific tags:${NC}"
        echo -e "   - ${GREEN}latest-mac${NC} (for macOS)"
        echo -e "   - ${GREEN}latest-jetson-orin${NC} (for Jetson Orin)"
        echo -e "   - ${GREEN}latest-jetson-thor${NC} (for Jetson Thor)"
    else
        echo -e "${GREEN}✅ Available versions:${NC}"
        echo ""

        # Display differently based on source
        if [ "$source" = "ghcr" ]; then
            # GHCR has actual container tags - separate base and platform-specific
            local base_versions=$(echo "$versions" | grep -E '^[0-9]+\.[0-9]+(\.[0-9]+)?$|^latest$' | head -20)
            local platform_versions=$(echo "$versions" | grep -E -- '-(mac|jetson)' | head -20)

            if [ -n "$base_versions" ]; then
                echo -e "${BLUE}Base versions (multi-arch):${NC}"
                echo "$base_versions" | while read -r version; do
                    echo -e "   - ${GREEN}${version}${NC}"
                done
                echo ""
            fi

            if [ -n "$platform_versions" ]; then
                echo -e "${BLUE}Platform-specific versions:${NC}"
                echo "$platform_versions" | while read -r version; do
                    echo -e "   - ${GREEN}${version}${NC}"
                done
                echo ""
            fi
        else
            # Releases API only has base versions - explain platform suffixes
            echo -e "${BLUE}Base versions (from GitHub Releases):${NC}"
            echo "$versions" | head -10 | while read -r version; do
                echo -e "   - ${GREEN}${version}${NC}"
            done
            echo ""

            echo -e "${BLUE}Platform-specific versions (inferred):${NC}"
            echo -e "   ${YELLOW}Note: Docker workflow creates these automatically for each release${NC}"
            echo -e "   Each base version also available with platform suffix:"
            echo -e "   - ${GREEN}<version>-mac${NC} (e.g., 0.1.1-mac)"
            echo -e "   - ${GREEN}<version>-jetson-orin${NC} (e.g., 0.1.1-jetson-orin)"
            echo -e "   - ${GREEN}<version>-jetson-thor${NC} (e.g., 0.1.1-jetson-thor)"
            echo ""
            echo -e "   Latest platform tags:"
            echo -e "   - ${GREEN}latest-mac${NC}"
            echo -e "   - ${GREEN}latest-jetson-orin${NC}"
            echo -e "   - ${GREEN}latest-jetson-thor${NC}"
            echo ""
        fi
    fi

    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
}

# Interactive version picker
pick_version() {
    local platform_suffix="$1"

    # All display output goes to stderr so it's not captured by $()
    echo -e "${YELLOW}🔍 Fetching available versions...${NC}" >&2
    local versions=$(fetch_available_versions 2>/dev/null)

    # Filter versions by platform
    local filtered_versions=""
    if [ -n "$platform_suffix" ]; then
        # Get platform-specific versions
        filtered_versions=$(echo "$versions" | grep -E "^[0-9]+\.[0-9]+(\.[0-9]+)?${platform_suffix}$|^latest${platform_suffix}$" | head -10)
        # Also add base versions if it's not a platform-specific suffix
        if [ "$platform_suffix" = "" ]; then
            local base_versions=$(echo "$versions" | grep -E '^[0-9]+\.[0-9]+(\.[0-9]+)?$|^latest$' | head -10)
            filtered_versions="${base_versions}"
        fi
    else
        filtered_versions=$(echo "$versions" | grep -E '^[0-9]+\.[0-9]+(\.[0-9]+)?$|^latest$' | head -10)
    fi

    if [ -z "$filtered_versions" ]; then
        echo -e "${YELLOW}⚠️  Could not fetch versions from registry${NC}" >&2
        echo -e "${YELLOW}   Showing common versions${NC}" >&2
        echo "" >&2

        # Show default list
        filtered_versions="latest"$'\n'"0.1.1"$'\n'"0.1.0"
    fi

    echo -e "${GREEN}Available versions:${NC}" >&2
    local version_array=()
    local index=1

    # Build array and display
    while IFS= read -r version; do
        version_array+=("$version")
        if [ "$version" = "latest" ]; then
            echo -e "  ${BLUE}[${index}]${NC} ${GREEN}${version}${NC} ${YELLOW}(recommended)${NC}" >&2
        else
            echo -e "  ${BLUE}[${index}]${NC} ${GREEN}${version}${NC}" >&2
        fi
        ((index++))
    done <<< "$filtered_versions"

    echo "" >&2
    echo -e "${YELLOW}💡 Tip: Use --version flag to skip this prompt${NC}" >&2
    echo -e "   Example: $0 --version 0.2.0" >&2
    echo "" >&2

    # Get user selection
    while true; do
        read -p "Select version number [1] or enter custom version: " selection >&2

        # Default to 1 (latest) if empty
        if [ -z "$selection" ]; then
            selection="1"
        fi

        # Check if it's a number selection
        if [[ "$selection" =~ ^[0-9]+$ ]] && [ "$selection" -ge 1 ] && [ "$selection" -le "${#version_array[@]}" ]; then
            local selected_index=$((selection - 1))
            echo "${version_array[$selected_index]}"
            return
        else
            # Treat as custom version string
            echo "$selection"
            return
        fi
    done
}

# Handle --list-versions flag
if [ "$LIST_VERSIONS" = true ]; then
    list_versions
    exit 0
fi

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}    Live-VLM-WebUI Docker Container Starter${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# ==============================================================================
# Check Prerequisites
# ==============================================================================
echo -e "${YELLOW}🔍 Checking Docker installation...${NC}"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker not found!${NC}"
    echo ""
    echo -e "${YELLOW}Docker is required to run this application.${NC}"
    echo ""
    echo -e "Install Docker:"
    echo -e "  Linux:   ${BLUE}https://docs.docker.com/engine/install/${NC}"
    echo -e "  Mac:     ${BLUE}https://docs.docker.com/desktop/install/mac-install/${NC}"
    echo -e "  Windows: ${BLUE}https://docs.docker.com/desktop/install/windows-install/${NC}"
    echo ""
    exit 1
fi

# Check if Docker daemon is running
if ! docker info &> /dev/null; then
    echo -e "${RED}❌ Docker daemon is not running!${NC}"
    echo ""
    echo -e "${YELLOW}Start Docker:${NC}"
    echo -e "  Linux:   ${GREEN}sudo systemctl start docker${NC}"
    echo -e "  Mac/Win: ${GREEN}Open Docker Desktop${NC}"
    echo ""
    exit 1
fi

echo -e "${GREEN}✅ Docker installed: $(docker --version)${NC}"
echo ""

# Detect architecture and OS
ARCH=$(uname -m)
OS=$(uname -s)
echo -e "${YELLOW}🔍 Detecting platform...${NC}"
echo -e "   Architecture: ${GREEN}${ARCH}${NC}"
echo -e "   OS: ${GREEN}${OS}${NC}"

# Detect platform type
PLATFORM="unknown"
BASE_TAG="latest"
PLATFORM_SUFFIX=""
GPU_FLAG=""
RUNTIME_FLAG=""

# Check if running on macOS
if [ "$OS" = "Darwin" ]; then
    PLATFORM="mac"
    PLATFORM_SUFFIX="-mac"
    GPU_FLAG=""  # No GPU support on Mac Docker
    echo -e "   Platform: ${GREEN}macOS (Apple Silicon)${NC}"
    echo ""
    echo -e "${YELLOW}⚠️  Note: Docker on Mac runs in a Linux VM${NC}"
    echo -e "${YELLOW}   - No Metal GPU access${NC}"
    echo -e "${YELLOW}   - Container will connect to Ollama on host${NC}"
    echo -e "${YELLOW}   - For best performance, use native Python instead!${NC}"
    echo -e "${YELLOW}     See: docs/cursor/MAC_SETUP.md${NC}"
    echo ""

    # Check if Ollama is running on host
    if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo -e "${RED}❌ Ollama not detected on host!${NC}"
        echo -e "${YELLOW}   Start Ollama first:${NC}"
        echo -e "   ${GREEN}ollama serve &${NC}"
        echo -e "   ${GREEN}ollama pull llama3.2-vision:11b${NC}"
        echo ""
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    else
        echo -e "${GREEN}✅ Ollama detected on host${NC}"
    fi

elif [ "$ARCH" = "x86_64" ]; then
    PLATFORM="x86"
    PLATFORM_SUFFIX=""
    GPU_FLAG="--gpus all"
    echo -e "   Platform: ${GREEN}PC (x86_64)${NC}"

elif [ "$ARCH" = "aarch64" ]; then
    # Check if it's a Jetson (has L4T)
    if [ -f /etc/nv_tegra_release ]; then
        # Read L4T version
        L4T_VERSION=$(head -n 1 /etc/nv_tegra_release | grep -oP 'R\K[0-9]+')

        # Check for Thor (L4T R38+) vs Orin (L4T R36)
        if [ "$L4T_VERSION" -ge 38 ]; then
            PLATFORM="jetson-thor"
            PLATFORM_SUFFIX="-jetson-thor"
            GPU_FLAG="--gpus all"
            echo -e "   Platform: ${GREEN}NVIDIA Jetson Thor${NC} (L4T R${L4T_VERSION})"
        else
            PLATFORM="jetson-orin"
            PLATFORM_SUFFIX="-jetson-orin"
            RUNTIME_FLAG="--runtime nvidia"
            echo -e "   Platform: ${GREEN}NVIDIA Jetson Orin${NC} (L4T R${L4T_VERSION})"
        fi
    else
        # ARM64 SBSA (DGX Spark, ARM servers)
        # Check if NVIDIA GPU is available
        if command -v nvidia-smi &> /dev/null && nvidia-smi &> /dev/null; then
            PLATFORM="arm64-sbsa"
            PLATFORM_SUFFIX=""  # Multi-arch image (works on both x86 and ARM64)
            GPU_FLAG="--gpus all"

            # Check if it's specifically DGX Spark
            if [ -f /etc/dgx-release ]; then
                DGX_NAME=$(grep -oP 'DGX_NAME="\K[^"]+' /etc/dgx-release 2>/dev/null || echo "DGX")
                DGX_VERSION=$(grep -oP 'DGX_SWBUILD_VERSION="\K[^"]+' /etc/dgx-release 2>/dev/null || echo "")
                if [ -n "$DGX_VERSION" ]; then
                    echo -e "   Platform: ${GREEN}NVIDIA ${DGX_NAME}${NC} (Version ${DGX_VERSION})"
                else
                    echo -e "   Platform: ${GREEN}NVIDIA ${DGX_NAME}${NC}"
                fi
            else
                echo -e "   Platform: ${GREEN}ARM64 SBSA with NVIDIA GPU${NC} (ARM server)"
            fi
            echo -e "   ${YELLOW}Note: Using multi-arch CUDA container${NC}"
        else
            echo -e "${RED}❌ ARM64 platform detected without NVIDIA GPU${NC}"
            echo -e "${RED}   Supported: x86 PC, DGX Spark, Jetson Thor/Orin${NC}"
            exit 1
        fi
    fi
else
    echo -e "${RED}❌ Unsupported architecture: ${ARCH}${NC}"
    exit 1
fi

echo ""

# ==============================================================================
# Version Selection
# ==============================================================================
if [ -n "$REQUESTED_VERSION" ]; then
    # User specified version via --version flag
    SELECTED_VERSION="$REQUESTED_VERSION"
    echo -e "${GREEN}✅ Using specified version: ${SELECTED_VERSION}${NC}"
elif [ "$SKIP_VERSION_CHECK" = true ]; then
    # User wants to skip and use latest
    SELECTED_VERSION="latest"
    echo -e "${GREEN}✅ Using latest version${NC}"
else
    # Interactive mode - let user pick version
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}    Select Docker Image Version${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo ""

    SELECTED_VERSION=$(pick_version "$PLATFORM_SUFFIX")

    echo ""
    echo -e "${GREEN}✅ Selected version: ${SELECTED_VERSION}${NC}"
fi

# Construct the final image tag
# If selected version already has platform suffix, use as-is
# Otherwise, append platform suffix if needed
if [[ "$SELECTED_VERSION" =~ -mac$|-jetson-orin$|-jetson-thor$ ]]; then
    # Version already has platform suffix
    IMAGE_TAG="$SELECTED_VERSION"
elif [ "$SELECTED_VERSION" = "latest" ] && [ -n "$PLATFORM_SUFFIX" ]; then
    # Latest with platform suffix
    IMAGE_TAG="latest${PLATFORM_SUFFIX}"
elif [[ "$SELECTED_VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] && [ -n "$PLATFORM_SUFFIX" ]; then
    # Semver with platform suffix (supports both X.Y and X.Y.Z)
    IMAGE_TAG="${SELECTED_VERSION}${PLATFORM_SUFFIX}"
else
    # Use as-is (multi-arch image or custom tag)
    IMAGE_TAG="$SELECTED_VERSION"
fi

echo ""

# Container name
CONTAINER_NAME="live-vlm-webui"

# Set image name based on platform
# All platforms now use registry images
IMAGE_NAME="ghcr.io/nvidia-ai-iot/live-vlm-webui:${IMAGE_TAG}"

echo -e "${BLUE}🐳 Docker Image: ${GREEN}${IMAGE_NAME}${NC}"

# Check if container already exists
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${YELLOW}⚠️  Container '${CONTAINER_NAME}' already exists${NC}"
    read -p "   Stop and remove it? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}🛑 Stopping and removing existing container...${NC}"
        docker stop ${CONTAINER_NAME} 2>/dev/null || true
        docker rm ${CONTAINER_NAME} 2>/dev/null || true
    else
        echo -e "${RED}❌ Aborted${NC}"
        exit 1
    fi
fi

# Pull latest image from registry (optional)
read -p "Pull latest image from registry? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${BLUE}📥 Pulling ${IMAGE_NAME}...${NC}"
    docker pull ${IMAGE_NAME} || {
        echo -e "${YELLOW}⚠️  Failed to pull from registry, will try local image${NC}"
    }
fi

# Check if image exists (registry or local)
if ! docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^${IMAGE_NAME}$"; then
    # Try common local image names with the same tag
    LOCAL_IMAGE=""
    LOCAL_TAG="live-vlm-webui:${IMAGE_TAG}"

    if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^${LOCAL_TAG}$"; then
        LOCAL_IMAGE="$LOCAL_TAG"
    else
        # Try platform-specific fallback tags for local builds
        if [ "$PLATFORM" = "mac" ]; then
            # Check for Mac local builds
            if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^live-vlm-webui:latest-mac$"; then
                LOCAL_IMAGE="live-vlm-webui:latest-mac"
            fi
        elif [ "$PLATFORM" = "arm64-sbsa" ]; then
            # Check for DGX Spark specific tags
            if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^live-vlm-webui:dgx-spark$"; then
                LOCAL_IMAGE="live-vlm-webui:dgx-spark"
            elif docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^live-vlm-webui:arm64$"; then
                LOCAL_IMAGE="live-vlm-webui:arm64"
            fi
        elif [ "$PLATFORM" = "x86" ]; then
            if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^live-vlm-webui:x86$"; then
                LOCAL_IMAGE="live-vlm-webui:x86"
            fi
        fi
    fi

    if [ -n "$LOCAL_IMAGE" ]; then
        echo -e "${GREEN}✅ Found local image: ${LOCAL_IMAGE}${NC}"
        IMAGE_NAME="${LOCAL_IMAGE}"
    else
        echo -e "${RED}❌ Image '${IMAGE_NAME}' not found${NC}"
        echo -e "${YELLOW}   Build it first with:${NC}"
        if [ "$PLATFORM" = "mac" ]; then
            echo -e "   ${GREEN}docker build -f docker/Dockerfile.mac -t ${IMAGE_NAME} .${NC}"
            echo -e "   ${YELLOW}Or pull from registry:${NC}"
            echo -e "   ${GREEN}docker pull ${IMAGE_NAME}${NC}"
        elif [ "$PLATFORM" = "arm64-sbsa" ]; then
            echo -e "   ${GREEN}docker build -f docker/Dockerfile -t ${IMAGE_NAME} .${NC}"
            echo -e "   ${YELLOW}Or pull from registry:${NC}"
            echo -e "   ${GREEN}docker pull ${IMAGE_NAME}${NC}"
        elif [ "$PLATFORM" = "jetson-thor" ]; then
            echo -e "   ${GREEN}docker build -f docker/Dockerfile.jetson-thor -t ${IMAGE_NAME} .${NC}"
            echo -e "   ${YELLOW}Or pull from registry:${NC}"
            echo -e "   ${GREEN}docker pull ${IMAGE_NAME}${NC}"
        elif [ "$PLATFORM" = "jetson-orin" ]; then
            echo -e "   ${GREEN}docker build -f docker/Dockerfile.jetson-orin -t ${IMAGE_NAME} .${NC}"
            echo -e "   ${YELLOW}Or pull from registry:${NC}"
            echo -e "   ${GREEN}docker pull ${IMAGE_NAME}${NC}"
        else
            echo -e "   ${GREEN}docker build -f docker/Dockerfile -t ${IMAGE_NAME} .${NC}"
            echo -e "   ${YELLOW}Or pull from registry:${NC}"
            echo -e "   ${GREEN}docker pull ${IMAGE_NAME}${NC}"
        fi
        exit 1
    fi
else
    echo -e "${GREEN}✅ Using image: ${IMAGE_NAME}${NC}"
fi

# Build run command based on platform
echo -e "${BLUE}🚀 Starting container...${NC}"

if [ "$PLATFORM" = "mac" ]; then
    # Mac-specific configuration
    # - Use port mapping (not host network)
    # - Connect to Ollama on host via host.docker.internal
    # - No GPU flags needed

    # Detect Mac system info to pass to container
    MAC_HOSTNAME=$(hostname -s)
    MAC_CHIP=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Apple Silicon")
    MAC_PRODUCT_NAME=$(system_profiler SPHardwareDataType 2>/dev/null | grep "Model Name" | awk -F': ' '{print $2}' || echo "Mac")

    DOCKER_CMD="docker run -d \
      --name ${CONTAINER_NAME} \
      -p 8090:8090 \
      -e VLM_API_BASE=http://host.docker.internal:11434/v1 \
      -e VLM_MODEL=llama3.2-vision:11b \
      -e HOST_HOSTNAME=${MAC_HOSTNAME} \
      -e HOST_PRODUCT_NAME=${MAC_PRODUCT_NAME} \
      -e HOST_CPU_MODEL=${MAC_CHIP} \
      ${IMAGE_NAME}"

    # Show Mac-specific notice
    echo ""
    echo -e "${YELLOW}⚠️  Mac Docker Limitation:${NC}"
    echo -e "${YELLOW}   WebRTC camera does NOT work in Docker on Mac (Docker Desktop limitation)${NC}"
    echo -e "${YELLOW}   The container will start and connect to Ollama, but camera will fail.${NC}"
    echo ""
    echo -e "${GREEN}💡 For camera support on Mac, run natively instead:${NC}"
    echo -e "${GREEN}   ./scripts/start_server.sh${NC}"
    echo -e "${GREEN}   # Or manually:${NC}"
    echo -e "${GREEN}   python3 -m live_vlm_webui.server --host 0.0.0.0 --port 8090 \\${NC}"
    echo -e "${GREEN}     --ssl-cert cert.pem --ssl-key key.pem \\${NC}"
    echo -e "${GREEN}     --api-base http://localhost:11434/v1 --model llama3.2-vision:11b${NC}"
    echo ""
    read -p "Continue with Docker anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Aborted. Run natively for full functionality.${NC}"
        exit 0
    fi
else
    # Linux (PC, Jetson) configuration
    DOCKER_CMD="docker run -d \
      --name ${CONTAINER_NAME} \
      --network host \
      --privileged"

    # Add GPU/runtime flags
    if [ -n "$GPU_FLAG" ]; then
        DOCKER_CMD="$DOCKER_CMD $GPU_FLAG"
    fi
    if [ -n "$RUNTIME_FLAG" ]; then
        DOCKER_CMD="$DOCKER_CMD $RUNTIME_FLAG"
    fi

    # Add DGX Spark-specific mounts
    if [ "$PLATFORM" = "arm64-sbsa" ] && [ -f /etc/dgx-release ]; then
        DOCKER_CMD="$DOCKER_CMD -v /etc/dgx-release:/etc/dgx-release:ro"
    fi

    # Add Jetson-specific mounts
    if [[ "$PLATFORM" == "jetson-"* ]]; then
        DOCKER_CMD="$DOCKER_CMD -v /run/jtop.sock:/run/jtop.sock:ro"
    fi

    # Add image name
    DOCKER_CMD="$DOCKER_CMD ${IMAGE_NAME}"
fi

# Execute
echo -e "${YELLOW}   Command: ${DOCKER_CMD}${NC}"
eval $DOCKER_CMD

# Wait a moment for container to start
sleep 2

# Check if container is running
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${GREEN}✅ Container started successfully!${NC}"
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}🌐 Access the Web UI at:${NC}"

    # Get IP addresses
    if command -v hostname &> /dev/null; then
        HOSTNAME=$(hostname)
        echo -e "   Local:   ${GREEN}https://localhost:8090${NC}"

        # Try to get network IP
        if command -v hostname &> /dev/null; then
            NETWORK_IP=$(hostname -I | awk '{print $1}')
            if [ -n "$NETWORK_IP" ]; then
                echo -e "   Network: ${GREEN}https://${NETWORK_IP}:8090${NC}"
            fi
        fi
    fi

    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${YELLOW}📋 Useful commands:${NC}"
    echo -e "   View logs:        ${GREEN}docker logs -f ${CONTAINER_NAME}${NC}"
    echo -e "   Stop container:   ${GREEN}docker stop ${CONTAINER_NAME}${NC}"
    echo -e "   Remove container: ${GREEN}docker rm ${CONTAINER_NAME}${NC}"
    echo ""
else
    echo -e "${RED}❌ Container failed to start${NC}"
    echo -e "${YELLOW}📋 Check logs with: docker logs ${CONTAINER_NAME}${NC}"
    exit 1
fi
