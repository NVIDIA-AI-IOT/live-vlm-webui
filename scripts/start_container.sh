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

    if ! command -v curl &> /dev/null || ! command -v jq &> /dev/null; then
        return 1
    fi

    if [ -z "$GITHUB_TOKEN" ]; then
        return 1
    fi

    local response=""
    local http_code=""

    # Fetch with HTTP status code and timeout (10 seconds should be enough)
    # Use --max-time to prevent hanging on slow networks
    response=$(curl -s --max-time 10 -w "\n%{http_code}" -H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github.v3+json" "${api_url}" 2>/dev/null)

    # Extract HTTP status code (last line)
    http_code=$(echo "$response" | tail -n1)
    response=$(echo "$response" | sed '$d')

    # Check HTTP status
    if [ "$http_code" != "200" ]; then
        # Check if it's a rate limit error (403)
        if [ "$http_code" = "403" ] && echo "$response" | grep -qi "rate limit"; then
            return 2  # Special return code for rate limit
        fi
        return 1
    fi

    # Check if response contains error message
    if echo "$response" | grep -qi '"message"'; then
        # Check if it's a rate limit message
        if echo "$response" | grep -qi "rate limit"; then
            return 2
        fi
        return 1
    fi

    # Check if response is valid JSON array (starts with [)
    if ! echo "$response" | grep -q '^\['; then
        return 1
    fi

    # Extract tags from metadata
    local parsed=$(echo "$response" | jq -r '.[].metadata.container.tags[]?' 2>/dev/null | grep -v '^null$' | sort -V -r | uniq)

    if [ -n "$parsed" ]; then
        echo "$parsed"
        return 0
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

    if ! command -v curl &> /dev/null; then
        return 1
    fi

    local response=""
    local http_code=""

    # Use GITHUB_TOKEN if available for higher rate limits (but don't require it)
    # Add timeout to prevent hanging (10 seconds)
    if [ -n "$GITHUB_TOKEN" ] && [ "$SIMULATE_PUBLIC" != "true" ]; then
        response=$(curl -s --max-time 10 -w "\n%{http_code}" -H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github.v3+json" "${api_url}" 2>/dev/null)
    else
        # Public API - works without auth (60 requests/hour limit)
        response=$(curl -s --max-time 10 -w "\n%{http_code}" -H "Accept: application/vnd.github.v3+json" "${api_url}" 2>/dev/null)
    fi

    # Extract HTTP status code (last line)
    http_code=$(echo "$response" | tail -n1)
    response=$(echo "$response" | sed '$d')

    # Check HTTP status
    if [ "$http_code" != "200" ]; then
        # Check if it's a rate limit error (403)
        if [ "$http_code" = "403" ] && echo "$response" | grep -qi "rate limit"; then
            return 2  # Special return code for rate limit
        fi
        return 1
    fi

    # Check if we hit rate limit in response body
    if echo "$response" | grep -qi '"message.*rate limit"'; then
        return 2  # Special return code for rate limit
    fi

    if [ -n "$response" ]; then
        # Check if response is valid JSON array (starts with [)
        if echo "$response" | grep -q '^\['; then
            if command -v jq &> /dev/null; then
                # Parse with jq - extract tag_name and remove 'v' prefix
                local parsed=$(echo "$response" | jq -r '.[].tag_name' 2>/dev/null | sed 's/^v//' | sort -V -r | uniq)
                if [ -n "$parsed" ]; then
                    echo "$parsed"
                    return 0
                fi
            else
                # Parse manually without jq
                local parsed=$(echo "$response" | grep -o '"tag_name":"[^"]*"' | cut -d'"' -f4 | sed 's/^v//' | sort -V -r | uniq)
                if [ -n "$parsed" ]; then
                    echo "$parsed"
                    return 0
                fi
            fi
        fi
    fi

    return 1
}

# Hybrid version fetcher: Try GHCR first, fall back to Releases API
fetch_available_versions() {
    local versions=""
    local source=""
    local rate_limited=false
    local ghcr_versions=""  # Store GHCR versions if they only contain latest/main (to merge with Releases)

    # Try GHCR API first if GITHUB_TOKEN is available (and not simulating public)
    if [ -n "$GITHUB_TOKEN" ] && [ "$SIMULATE_PUBLIC" != "true" ]; then
        # Use a temp file to capture both output and return code
        local temp_file=$(mktemp)
        fetch_versions_from_ghcr > "$temp_file" 2>&1
        local ghcr_result=$?
        versions=$(cat "$temp_file")
        rm -f "$temp_file"

        if [ "$ghcr_result" = "2" ]; then
            # Rate limited
            rate_limited=true
        elif [ "$ghcr_result" = "0" ] && [ -n "$versions" ]; then
            # Check if versions look valid (not error messages)
            # Reject if it looks like an error message (contains "message", "error", "403", etc.)
            if echo "$versions" | grep -qiE '(message|error|403|401|unauthorized|bad credentials)'; then
                # This is an error message, not versions
                versions=""
            elif echo "$versions" | grep -qE '(latest|main|[0-9])'; then
                # Check if GHCR has numbered versions WITHOUT platform suffixes (base versions like "0.2.1")
                # If it only has latest/main or platform-specific versions, we should fall back to Releases API
                # for base numbered versions (which we can then construct into platform-specific tags)
                if echo "$versions" | grep -qE '^[0-9]+\.[0-9]+(\.[0-9]+)?$'; then
                    # GHCR has base numbered versions (without platform suffix), use it
                    source="ghcr"
                    echo "$versions"
                    return 0
                fi
                # GHCR only has latest/main or platform-specific versions, save it and merge with Releases API results
                ghcr_versions="$versions"
            fi
        fi
    fi

    # Fall back to public Releases API
    # Use a temp file to capture both output and check for errors
    local temp_file=$(mktemp)
    fetch_versions_from_releases > "$temp_file" 2>&1
    local fetch_result=$?
    versions=$(cat "$temp_file")
    rm -f "$temp_file"

    # Check if response contains rate limit message
    if echo "$versions" | grep -qi "rate limit"; then
        rate_limited=true
    elif [ "$fetch_result" = "0" ] && [ -n "$versions" ] && ! echo "$versions" | grep -qi "error\|message"; then
        # Merge GHCR versions (latest/main) with Releases API versions (numbered) if GHCR had versions
        if [ -n "$ghcr_versions" ]; then
            # Combine: GHCR versions (latest/main) + Releases versions (numbered)
            versions=$(echo -e "$ghcr_versions\n$versions" | sort -V -r | uniq)
            source="ghcr+releases"
        else
            source="releases"
        fi
        echo "$versions"
        return 0
    fi

    # Both failed - return special code if rate limited
    if [ "$rate_limited" = true ]; then
        return 2
    fi

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
        # Escape the platform suffix for regex and use -- to prevent - from being interpreted as option
        local escaped_suffix=$(echo "$platform_suffix" | sed 's/[[\.*^$()+?{|]/\\&/g')

        # First, try to get platform-specific versions directly (from GHCR API)
        # Pattern matches: latest-PLATFORM, main-PLATFORM, X.Y-PLATFORM, X.Y.Z-PLATFORM
        local platform_specific=$(echo "$versions" | grep -E -- "${escaped_suffix}\$" | sort -V -r)
        # Exclude main-* versions but keep latest-* and numbered versions (X.Y or X.Y.Z)
        platform_specific=$(echo "$platform_specific" | grep -vE -- '^main' | head -20)

        # Get base versions (numbered versions, exclude main/latest) - always check this
        # This handles the Releases API case where we get "0.2.1", "0.2.0", etc.
        local base_versions=$(echo "$versions" | grep -E '^[0-9]+\.[0-9]+(\.[0-9]+)?$' | sort -V -r | head -20)

        # Check if we have numbered platform-specific versions (not just latest)
        local has_numbered_platform=$(echo "$platform_specific" | grep -E -- '^[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)

        # Strategy: Always prefer constructing from base versions if available (more reliable)
        # Only use platform_specific if it has numbered versions AND we don't have base versions
        if [ -n "$base_versions" ]; then
            # Construct platform-specific tags from base versions (Releases API case)
            filtered_versions=$(echo "$base_versions" | while read -r version; do
                echo "${version}${platform_suffix}"
            done)
            # Always add latest-PLATFORM at the beginning if it exists
            if echo "$platform_specific" | grep -qE '^latest'; then
                # latest exists in platform_specific, prepend it
                filtered_versions="latest${platform_suffix}"$'\n'"${filtered_versions}"
            elif echo "$versions" | grep -qE '^latest$'; then
                # Construct latest from base versions, prepend it
                filtered_versions="latest${platform_suffix}"$'\n'"${filtered_versions}"
            fi
        elif [ -n "$has_numbered_platform" ]; then
            # We have numbered platform-specific versions from GHCR, use them
            filtered_versions="$platform_specific"
        elif [ -n "$platform_specific" ]; then
            # Only have platform_specific (like latest-jetson-orin) but no base versions
            filtered_versions="$platform_specific"
        elif echo "$versions" | grep -qE '^latest$'; then
            # Only have latest in base versions, construct it
            filtered_versions="latest${platform_suffix}"
        fi

        # If still no versions, try a more permissive pattern
        if [ -z "$filtered_versions" ]; then
            filtered_versions=$(echo "$versions" | grep -E -- ".*${escaped_suffix}" | sort -V -r | head -15)
        fi
    else
        filtered_versions=$(echo "$versions" | grep -E '^[0-9]+\.[0-9]+(\.[0-9]+)?$|^latest$' | head -10)
    fi

    if [ -z "$filtered_versions" ]; then
        # Check if it was a rate limit issue by testing the APIs directly
        local test_response=""
        local test_ghcr_response=""

        # Test GHCR API if token is available
        if [ -n "$GITHUB_TOKEN" ] && [ "$SIMULATE_PUBLIC" != "true" ]; then
            test_ghcr_response=$(curl -s --max-time 5 -w "\n%{http_code}" -H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github.v3+json" "https://api.github.com/orgs/nvidia-ai-iot/packages/container/live-vlm-webui/versions" 2>/dev/null)
            test_response=$(curl -s --max-time 5 -H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github.v3+json" "https://api.github.com/repos/nvidia-ai-iot/live-vlm-webui/releases" 2>/dev/null)
        else
            test_response=$(curl -s --max-time 5 -H "Accept: application/vnd.github.v3+json" "https://api.github.com/repos/nvidia-ai-iot/live-vlm-webui/releases" 2>/dev/null)
        fi

        # Check GHCR response
        if [ -n "$test_ghcr_response" ]; then
            local ghcr_http_code=$(echo "$test_ghcr_response" | tail -n1)
            local ghcr_body=$(echo "$test_ghcr_response" | sed '$d')
            if [ "$ghcr_http_code" = "403" ] || echo "$ghcr_body" | grep -qi "rate limit"; then
                echo -e "${RED}⚠️  GitHub API rate limit exceeded${NC}" >&2
                echo -e "${YELLOW}   Even with GITHUB_TOKEN, rate limit reached${NC}" >&2
                echo "" >&2
            elif [ "$ghcr_http_code" = "401" ] || echo "$ghcr_body" | grep -qi "bad credentials\|unauthorized"; then
                echo -e "${YELLOW}⚠️  GitHub token authentication failed${NC}" >&2
                echo -e "${YELLOW}   Token may need 'read:packages' scope for GHCR access${NC}" >&2
                echo "" >&2
            fi
        fi

        if echo "$test_response" | grep -qi "rate limit"; then
            echo -e "${RED}⚠️  GitHub API rate limit exceeded${NC}" >&2
            echo -e "${YELLOW}   Unauthenticated requests: 60/hour limit reached${NC}" >&2
            echo "" >&2
            echo -e "${BLUE}💡 Solutions:${NC}" >&2
            echo -e "${GREEN}   1. Use --skip-version-pick to use 'latest' automatically:${NC}" >&2
            echo -e "      ${GREEN}./scripts/start_container.sh --skip-version-pick${NC}" >&2
            echo "" >&2
            echo -e "${GREEN}   2. Set GITHUB_TOKEN for higher rate limits (5000/hour):${NC}" >&2
            echo -e "      ${GREEN}export GITHUB_TOKEN=ghp_xxxxxxxxxxxxx${NC}" >&2
            echo -e "      ${GREEN}./scripts/start_container.sh${NC}" >&2
            echo "" >&2
            echo -e "${GREEN}   3. Wait ~1 hour for rate limit to reset${NC}" >&2
            echo "" >&2
        else
            echo -e "${YELLOW}⚠️  Could not fetch versions from registry${NC}" >&2
            echo -e "${YELLOW}   Showing common versions${NC}" >&2
            echo "" >&2

            # Provide troubleshooting tips
            if ! command -v curl &> /dev/null; then
                echo -e "${RED}   ✗ curl is not installed${NC}" >&2
                echo -e "${YELLOW}   Install with: sudo apt install -y curl${NC}" >&2
            elif ! command -v jq &> /dev/null; then
                echo -e "${YELLOW}   ℹ️  jq not installed (optional, but helpful for better parsing)${NC}" >&2
                echo -e "${YELLOW}   Install with: sudo apt install -y jq${NC}" >&2
            fi

            # Test network connectivity
            if ! curl -s --max-time 5 https://api.github.com > /dev/null 2>&1; then
                echo -e "${RED}   ✗ Cannot reach GitHub API (network issue?)${NC}" >&2
            fi

            echo "" >&2
        fi

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

# Enable tracing to debug hangs (set DEBUG=1 to enable)
if [ "${DEBUG:-0}" = "1" ]; then
    set -x
fi

# Check for Docker using multiple methods (more robust)
# Check common locations FIRST (most reliable, works even if PATH is broken)
# Use timeouts to prevent hanging on slow filesystems or network mounts
DOCKER_CMD=""

# Method 1: Check common locations directly (fastest, most reliable)
# Try to actually execute docker --version instead of just checking [ -x ]
# This handles cases where the file exists but execve() fails (network mounts, FUSE issues)
# Add retry logic for intermittent filesystem issues
for docker_path in /usr/bin/docker /usr/local/bin/docker /snap/bin/docker; do
    # Retry up to 3 times with small delays to handle intermittent filesystem issues
    max_retries=3
    retry_count=0
    docker_test_result=1

    while [ "$retry_count" -lt "$max_retries" ] && [ "$docker_test_result" -ne 0 ]; do
        set +e  # Temporarily disable exit on error
        if command -v timeout &> /dev/null 2>&1; then
            # Try to actually execute docker --version (with timeout to prevent hanging)
            # This is more reliable than [ -x ] which can pass even when execve() fails
            docker_test_output=$(timeout 2 "$docker_path" --version 2>&1)
            docker_test_result=$?
        else
            docker_test_output=$("$docker_path" --version 2>&1)
            docker_test_result=$?
        fi
        set -e  # Re-enable exit on error

        # If docker --version succeeded, we found a working Docker
        if [ "$docker_test_result" -eq 0 ] && [ -n "$docker_test_output" ]; then
            DOCKER_CMD="$docker_path"
            break 2  # Break out of both loops
        fi

        # If not the last retry, wait a bit before retrying
        if [ "$retry_count" -lt $((max_retries - 1)) ]; then
            sleep 0.1  # Small delay (100ms) before retry
        fi
        retry_count=$((retry_count + 1))
    done

    # If we found Docker, break out of the outer loop
    if [ -n "$DOCKER_CMD" ]; then
        break
    fi
done

# Method 1b: Check snap installation more thoroughly
if [ -z "$DOCKER_CMD" ] && command -v snap &> /dev/null 2>&1; then
    # Check if docker snap is installed
    if snap list docker &> /dev/null 2>&1; then
        # Try to find docker in snap directories by actually executing it
        for snap_docker in /snap/docker/current/bin/docker /snap/bin/docker; do
            set +e  # Temporarily disable exit on error
            if command -v timeout &> /dev/null 2>&1; then
                docker_test_output=$(timeout 2 "$snap_docker" --version 2>&1)
                docker_test_result=$?
            else
                docker_test_output=$("$snap_docker" --version 2>&1)
                docker_test_result=$?
            fi
            set -e  # Re-enable exit on error

            if [ "$docker_test_result" -eq 0 ] && [ -n "$docker_test_output" ]; then
                DOCKER_CMD="$snap_docker"
                break
            fi
        done
    fi
fi

# Method 2: Try command -v (works if PATH is correct, but can hang)
if [ -z "$DOCKER_CMD" ]; then
    if command -v timeout &> /dev/null 2>&1; then
        found_path=$(timeout 2 command -v docker 2>/dev/null || true)
    else
        # No timeout - try but risk hanging
        found_path=$(command -v docker 2>/dev/null || true)
    fi
    # Verify the path actually exists and is executable
    if [ -n "$found_path" ] && [ -x "$found_path" ] 2>/dev/null; then
        DOCKER_CMD="$found_path"
    fi
fi

# Method 3: Try which (less reliable, can also hang)
if [ -z "$DOCKER_CMD" ] && command -v which &> /dev/null 2>&1; then
    if command -v timeout &> /dev/null 2>&1; then
        found_path=$(timeout 2 which docker 2>/dev/null || true)
    else
        found_path=$(which docker 2>/dev/null || true)
    fi
    # Verify the path actually exists and is executable
    if [ -n "$found_path" ] && [ -x "$found_path" ] 2>/dev/null; then
        DOCKER_CMD="$found_path"
    fi
fi

# Method 4: Search PATH directories manually (last resort, can be slow)
if [ -z "$DOCKER_CMD" ] && [ -n "$PATH" ]; then
    IFS=':' read -ra PATH_DIRS <<< "$PATH"
    for dir in "${PATH_DIRS[@]}"; do
        # Skip empty or problematic directories
        if [ -z "$dir" ] || [ "$dir" = "." ]; then
            continue
        fi
        # Check with timeout if available
        if command -v timeout &> /dev/null 2>&1; then
            if timeout 1 test -x "$dir/docker" 2>/dev/null; then
                DOCKER_CMD="$dir/docker"
                break
            fi
        else
            if test -x "$dir/docker" 2>/dev/null; then
                DOCKER_CMD="$dir/docker"
                break
            fi
        fi
    done
fi

# Final verification: Try to actually execute docker --version to ensure it works
# This catches cases where the file exists but is broken, on a broken mount, etc.
if [ -n "$DOCKER_CMD" ]; then
    set +e  # Temporarily disable exit on error for verification
    if command -v timeout &> /dev/null 2>&1; then
        # Try with timeout to prevent hanging
        docker_version_output=$(timeout 3 "$DOCKER_CMD" --version 2>&1)
        docker_verify_result=$?
    else
        docker_version_output=$("$DOCKER_CMD" --version 2>&1)
        docker_verify_result=$?
    fi
    set -e  # Re-enable exit on error

    # If docker --version failed, the path is invalid (broken symlink, mount issue, etc.)
    if [ "$docker_verify_result" -ne 0 ] || [ -z "$docker_version_output" ]; then
        # Clear DOCKER_CMD and let it fall through to "not found" error
        DOCKER_CMD=""
    fi
fi

if [ -z "$DOCKER_CMD" ]; then
    echo -e "${RED}❌ Docker not found!${NC}"
    echo ""
    echo -e "${YELLOW}Docker is required to run this application.${NC}"
    echo ""

    # Check if running on Jetson with JetPack 6 (R36) or JetPack 7 (R38+)
    # When using SDK Manager, Docker and NVIDIA Container Toolkit are not automatically installed
    IS_JETSON_R36=false
    IS_JETSON_R38=false

    if [ -f /etc/nv_tegra_release ]; then
        L4T_VERSION=$(head -n 1 /etc/nv_tegra_release | grep -oP 'R\K[0-9]+' 2>/dev/null || echo "")
        # Check if L4T_VERSION is numeric
        if [ -n "$L4T_VERSION" ] && [[ "$L4T_VERSION" =~ ^[0-9]+$ ]]; then
            if [ "$L4T_VERSION" -ge 36 ] && [ "$L4T_VERSION" -lt 38 ]; then
                IS_JETSON_R36=true
            elif [ "$L4T_VERSION" -ge 38 ]; then
                IS_JETSON_R38=true
            fi
        fi
    fi

    if [ "$IS_JETSON_R36" = true ] || [ "$IS_JETSON_R38" = true ]; then
        if [ "$IS_JETSON_R36" = true ]; then
            echo -e "${YELLOW}Detected Jetson Orin with JetPack 6 (L4T R${L4T_VERSION})${NC}"
        else
            echo -e "${YELLOW}Detected Jetson Thor with JetPack 7 (L4T R${L4T_VERSION})${NC}"
        fi
        echo -e "${YELLOW}When using SDK Manager, Docker and NVIDIA Container Toolkit are not automatically installed.${NC}"
        echo ""
        echo -e "${GREEN}Install Docker and NVIDIA Container Toolkit:${NC}"
        echo ""
        echo -e "${YELLOW}# If you encounter apt lock errors, resolve them first:${NC}"
        echo -e "${GREEN}sudo killall apt apt-get 2>/dev/null || true${NC}"
        echo -e "${GREEN}sudo rm /var/lib/apt/lists/lock /var/cache/apt/archives/lock /var/lib/dpkg/lock* 2>/dev/null || true${NC}"
        echo -e "${GREEN}sudo dpkg --configure -a${NC}"
        echo ""
        echo -e "${BLUE}# Install NVIDIA Container Toolkit and Docker${NC}"
        echo -e "${GREEN}sudo apt update${NC}"
        echo -e "${GREEN}sudo apt install -y nvidia-container curl${NC}"
        echo -e "${GREEN}curl https://get.docker.com | sh && sudo systemctl --now enable docker${NC}"
        echo ""
        echo -e "${BLUE}# Initialize Docker daemon.json if it doesn't exist or is empty${NC}"
        echo -e "${GREEN}if [ ! -s /etc/docker/daemon.json ]; then${NC}"
        echo -e "${GREEN}    echo '{}' | sudo tee /etc/docker/daemon.json${NC}"
        echo -e "${GREEN}fi${NC}"
        echo ""
        echo -e "${BLUE}# Configure NVIDIA Container Toolkit runtime${NC}"
        echo -e "${GREEN}sudo nvidia-ctk runtime configure --runtime=docker${NC}"
        echo ""
        echo -e "${BLUE}# Restart Docker and add user to docker group${NC}"
        echo -e "${GREEN}sudo systemctl restart docker${NC}"
        echo -e "${GREEN}sudo usermod -aG docker \$USER${NC}"
        echo -e "${GREEN}newgrp docker${NC}"
        echo ""
        echo -e "${BLUE}# Configure Docker to use NVIDIA runtime by default${NC}"
        echo -e "${GREEN}sudo apt install -y jq${NC}"
        echo -e "${GREEN}sudo jq '. + {\"default-runtime\": \"nvidia\"}' /etc/docker/daemon.json > /tmp/daemon.json.tmp && sudo mv /tmp/daemon.json.tmp /etc/docker/daemon.json${NC}"
        echo ""
        echo -e "${BLUE}# Restart Docker service${NC}"
        echo -e "${GREEN}sudo systemctl daemon-reload && sudo systemctl restart docker${NC}"
        echo ""
        echo -e "${YELLOW}Reference: ${BLUE}https://www.jetson-ai-lab.com/tips_ssd-docker.html#docker${NC}"
        echo ""
        echo -e "${YELLOW}⚠️  IMPORTANT: After completing the installation, reboot your Jetson:${NC}"
        echo -e "${GREEN}sudo reboot${NC}"
        echo ""
        echo -e "${YELLOW}This ensures Docker and NVIDIA Container Toolkit are properly initialized.${NC}"
    else
        echo -e "Install Docker:"
        echo -e "  Linux:   ${BLUE}https://docs.docker.com/engine/install/${NC}"
        echo -e "  Mac:     ${BLUE}https://docs.docker.com/desktop/install/mac-install/${NC}"
        echo -e "  Windows: ${BLUE}https://docs.docker.com/desktop/install/windows-install/${NC}"
    fi
    echo ""
    exit 1
fi

# Check if Docker daemon is running FIRST (before trying to connect)
DOCKER_DAEMON_RUNNING=false
if systemctl is-active --quiet docker 2>/dev/null; then
    DOCKER_DAEMON_RUNNING=true
elif pgrep -x dockerd > /dev/null 2>&1; then
    DOCKER_DAEMON_RUNNING=true
elif pgrep -f "docker daemon" > /dev/null 2>&1; then
    DOCKER_DAEMON_RUNNING=true
fi

# Check if Docker daemon is accessible
# Temporarily disable set -e for error handling (we'll check the result manually)
# Add retry logic for intermittent filesystem issues (same as Docker detection)
DOCKER_ERROR=""
docker_check_result=1
max_daemon_retries=3
daemon_retry_count=0

set +e  # Disable exit on error temporarily
while [ "$daemon_retry_count" -lt "$max_daemon_retries" ] && [ "$docker_check_result" -ne 0 ]; do
    # Use timeout to prevent hanging (5 seconds should be enough)
    if command -v timeout &> /dev/null 2>&1; then
        # Use timeout to prevent hanging
        timeout 5 "$DOCKER_CMD" info &> /dev/null 2>&1
        docker_check_result=$?
        if [ "$docker_check_result" -ne 0 ]; then
            DOCKER_ERROR=$(timeout 5 "$DOCKER_CMD" info 2>&1 || echo "timeout or error")
        else
            # Docker is working, continue
            DOCKER_ERROR=""
            break  # Success, exit retry loop
        fi
    else
        # No timeout command available, try without timeout
        "$DOCKER_CMD" info &> /dev/null 2>&1
        docker_check_result=$?
        if [ "$docker_check_result" -ne 0 ]; then
            DOCKER_ERROR=$("$DOCKER_CMD" info 2>&1 || echo "error")
        else
            # Docker is working, continue
            DOCKER_ERROR=""
            break  # Success, exit retry loop
        fi
    fi

    # If not the last retry, wait a bit before retrying
    if [ "$daemon_retry_count" -lt $((max_daemon_retries - 1)) ]; then
        sleep 0.2  # Small delay (200ms) before retry
    fi
    daemon_retry_count=$((daemon_retry_count + 1))
done
set -e  # Re-enable exit on error

if [ -n "$DOCKER_ERROR" ]; then

    # Check if it's a permission issue
    if echo "$DOCKER_ERROR" | grep -qi "permission denied\|cannot connect"; then
        echo -e "${RED}❌ Cannot connect to Docker daemon!${NC}"
        echo ""
        echo -e "${YELLOW}This is likely a permissions issue.${NC}"
        echo ""

        # Check if user is already in docker group
        if groups | grep -q docker; then
            echo -e "${YELLOW}⚠️  You are in the docker group, but Docker still can't connect.${NC}"
            echo ""

            # Use the daemon running check we did earlier
            if [ "$DOCKER_DAEMON_RUNNING" = true ]; then
                echo -e "${YELLOW}Docker daemon appears to be running.${NC}"
                echo ""
                echo -e "${BLUE}Possible issues:${NC}"
                echo -e "${YELLOW}  1. Socket permissions may be wrong${NC}"
                echo -e "${GREEN}     Check: ls -l /var/run/docker.sock${NC}"
                echo ""
                echo -e "${YELLOW}  2. Try running Docker directly:${NC}"
                echo -e "${GREEN}     $DOCKER_CMD ps${NC}"
                echo ""
                echo -e "${YELLOW}  3. Check Docker socket permissions:${NC}"
                echo -e "${GREEN}     sudo chown root:docker /var/run/docker.sock${NC}"
                echo -e "${GREEN}     sudo chmod 660 /var/run/docker.sock${NC}"
                echo ""
                echo -e "${YELLOW}  4. Verify your group membership is active:${NC}"
                echo -e "${GREEN}     id -nG${NC}"
                echo -e "${GREEN}     (should show 'docker' in the list)${NC}"
            else
                echo -e "${RED}Docker daemon is not running!${NC}"
                echo ""
                echo -e "${BLUE}Start Docker daemon:${NC}"
                echo -e "${GREEN}sudo systemctl start docker${NC}"
                echo ""
                echo -e "${YELLOW}Check Docker status:${NC}"
                echo -e "${GREEN}sudo systemctl status docker${NC}"
                echo ""
            fi

            echo -e "${BLUE}Alternative: Try activating docker group:${NC}"
            echo -e "${GREEN}sg docker -c \"$DOCKER_CMD ps\"${NC}"
            echo ""
            echo -e "${YELLOW}If that works, run the script with:${NC}"
            echo -e "${GREEN}sg docker -c \"./scripts/start_container.sh\"${NC}"
        else
            echo -e "${BLUE}Solution: Add your user to the docker group${NC}"
            echo -e "${GREEN}sudo usermod -aG docker \$USER${NC}"
            echo ""
            echo -e "${YELLOW}Then either:${NC}"
            echo -e "${GREEN}  1. Log out and log back in${NC}"
            echo -e "${GREEN}  2. Or run: newgrp docker${NC}"
            echo ""
            echo -e "${YELLOW}Then run this script again.${NC}"
        fi
        echo ""
        echo -e "${YELLOW}Alternative (not recommended): Run with sudo${NC}"
        echo -e "${GREEN}sudo $0${NC}"
        echo ""
    else
        echo -e "${RED}❌ Docker daemon is not running!${NC}"
        echo ""
        echo -e "${YELLOW}Start Docker:${NC}"
        echo -e "  Linux:   ${GREEN}sudo systemctl start docker${NC}"
        echo -e "  Mac/Win: ${GREEN}Open Docker Desktop${NC}"
        echo ""
        echo -e "${YELLOW}Check Docker status:${NC}"
        echo -e "  ${GREEN}sudo systemctl status docker${NC}"
        echo ""
    fi
    exit 1
fi

echo -e "${GREEN}✅ Docker installed: $($DOCKER_CMD --version)${NC}"
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
    L4T_VERSION=""
    if [ -f /etc/nv_tegra_release ] && [ -r /etc/nv_tegra_release ]; then
        # Read L4T version - try multiple methods for robustness
        # Method 1: grep -oP (Perl regex, most reliable)
        L4T_VERSION=$(head -n 1 /etc/nv_tegra_release | grep -oP 'R\K[0-9]+' 2>/dev/null || echo "")
        # Method 2: sed fallback if grep -oP fails
        if [ -z "$L4T_VERSION" ]; then
            L4T_VERSION=$(head -n 1 /etc/nv_tegra_release | sed -n 's/.*R\([0-9]\+\).*/\1/p' 2>/dev/null || echo "")
        fi
        # Method 3: grep with basic regex
        if [ -z "$L4T_VERSION" ]; then
            L4T_VERSION=$(head -n 1 /etc/nv_tegra_release | grep -oE 'R[0-9]+' | grep -oE '[0-9]+' | head -1 2>/dev/null || echo "")
        fi
    fi

    # Fallback: Check for Jetson indicators if L4T file is not accessible
    # This handles cases where MAX-N mode or other system changes affect file access
    if [ -z "$L4T_VERSION" ]; then
        # Check for Jetson-specific hardware indicators
        if [ -d /sys/devices/soc0 ] && grep -q "tegra" /sys/devices/soc0/family 2>/dev/null; then
            # It's a Jetson, but we can't read L4T version - default to Orin (most common)
            PLATFORM="jetson-orin"
            PLATFORM_SUFFIX="-jetson-orin"
            RUNTIME_FLAG="--runtime nvidia"
            echo -e "   Platform: ${GREEN}NVIDIA Jetson Orin${NC} (L4T version unavailable, detected via hardware)"
        elif command -v nvidia-smi &> /dev/null && nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -qi "jetson\|orin\|thor\|xavier\|nano"; then
            # Check GPU name for Jetson indicators
            GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
            if echo "$GPU_NAME" | grep -qi "thor"; then
                PLATFORM="jetson-thor"
                PLATFORM_SUFFIX="-jetson-thor"
                GPU_FLAG="--gpus all"
                echo -e "   Platform: ${GREEN}NVIDIA Jetson Thor${NC} (detected via GPU: ${GPU_NAME})"
            else
                PLATFORM="jetson-orin"
                PLATFORM_SUFFIX="-jetson-orin"
                RUNTIME_FLAG="--runtime nvidia"
                echo -e "   Platform: ${GREEN}NVIDIA Jetson Orin${NC} (detected via GPU: ${GPU_NAME})"
            fi
        fi
    fi

    # If we have L4T_VERSION, use it for precise detection
    if [ -n "$L4T_VERSION" ] && [[ "$L4T_VERSION" =~ ^[0-9]+$ ]]; then
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
    elif [ -z "$PLATFORM" ]; then
        # No Jetson detected, fall through to ARM64 SBSA check
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

# Check if container already exists (by name)
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

# Also check for containers using the same image (might have different names)
EXISTING_CONTAINERS=$(docker ps -a --filter "ancestor=${IMAGE_NAME}" --format "{{.Names}}" 2>/dev/null | grep -v "^${CONTAINER_NAME}$" || true)
if [ -n "$EXISTING_CONTAINERS" ]; then
    echo -e "${YELLOW}⚠️  Found other containers using the same image:${NC}"
    echo "$EXISTING_CONTAINERS" | while read -r name; do
        echo -e "   ${YELLOW}- ${name}${NC}"
    done
    echo ""
    read -p "   Stop and remove them? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "$EXISTING_CONTAINERS" | while read -r name; do
            echo -e "${YELLOW}   Stopping ${name}...${NC}"
            docker stop "$name" 2>/dev/null || true
            docker rm "$name" 2>/dev/null || true
        done
    fi
fi

# Pull latest image from registry (optional)
read -p "Pull latest image from registry? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${BLUE}📥 Pulling ${IMAGE_NAME}...${NC}"
    set +e  # Temporarily disable exit on error for pull operation
    docker pull ${IMAGE_NAME}
    pull_result=$?
    set -e  # Re-enable exit on error

    if [ "$pull_result" -ne 0 ]; then
        echo -e "${YELLOW}⚠️  Failed to pull from registry${NC}"

        # Check if Docker daemon is still accessible
        set +e
        docker info > /dev/null 2>&1
        daemon_check=$?
        set -e

        if [ "$daemon_check" -ne 0 ]; then
            echo -e "${RED}❌ Docker daemon connection lost!${NC}"
            echo ""
            echo -e "${YELLOW}The Docker daemon may have crashed or become unresponsive during the pull.${NC}"
            echo ""
            echo -e "${BLUE}Try restarting Docker:${NC}"
            echo -e "${GREEN}sudo systemctl restart docker${NC}"
            echo ""
            echo -e "${YELLOW}Then check Docker status:${NC}"
            echo -e "${GREEN}sudo systemctl status docker${NC}"
            echo ""
            echo -e "${YELLOW}If Docker keeps crashing, check system logs:${NC}"
            echo -e "${GREEN}sudo journalctl -u docker -n 50${NC}"
            echo ""
            echo -e "${YELLOW}Or check dmesg for filesystem errors:${NC}"
            echo -e "${GREEN}dmesg | tail -20${NC}"
            echo ""
            exit 1
        else
            echo -e "${YELLOW}   Will try local image${NC}"
        fi
    fi
fi

# Check if image exists (registry or local)
# Use timeout and error handling in case Docker daemon is unresponsive
set +e
image_check_output=$(timeout 5 docker images --format '{{.Repository}}:{{.Tag}}' 2>&1)
image_check_result=$?
set -e

if [ "$image_check_result" -ne 0 ]; then
    # Docker daemon check failed
    if echo "$image_check_output" | grep -qi "cannot connect\|daemon"; then
        echo -e "${RED}❌ Cannot connect to Docker daemon!${NC}"
        echo ""
        echo -e "${YELLOW}The Docker daemon may have crashed or become unresponsive.${NC}"
        echo ""
        echo -e "${BLUE}Try restarting Docker:${NC}"
        echo -e "${GREEN}sudo systemctl restart docker${NC}"
        echo ""
        exit 1
    fi
    # Some other error, continue and try to check anyway
fi

if ! echo "$image_check_output" | grep -q "^${IMAGE_NAME}$"; then
    # Try common local image names with the same tag
    LOCAL_IMAGE=""
    LOCAL_TAG="live-vlm-webui:${IMAGE_TAG}"

    # Use the already-fetched image list if available, otherwise fetch again with timeout
    if echo "$image_check_output" | grep -q "^${LOCAL_TAG}$"; then
        LOCAL_IMAGE="$LOCAL_TAG"
    else
        # Try platform-specific fallback tags for local builds
        if [ "$PLATFORM" = "mac" ]; then
            # Check for Mac local builds
            if echo "$image_check_output" | grep -q "^live-vlm-webui:latest-mac$"; then
                LOCAL_IMAGE="live-vlm-webui:latest-mac"
            fi
        elif [ "$PLATFORM" = "arm64-sbsa" ]; then
            # Check for DGX Spark specific tags
            if echo "$image_check_output" | grep -q "^live-vlm-webui:dgx-spark$"; then
                LOCAL_IMAGE="live-vlm-webui:dgx-spark"
            elif echo "$image_check_output" | grep -q "^live-vlm-webui:arm64$"; then
                LOCAL_IMAGE="live-vlm-webui:arm64"
            fi
        elif [ "$PLATFORM" = "x86" ]; then
            if echo "$image_check_output" | grep -q "^live-vlm-webui:x86$"; then
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
        # Check if jtop socket exists on host before mounting
        if [ -S /run/jtop.sock ]; then
            DOCKER_CMD="$DOCKER_CMD -v /run/jtop.sock:/run/jtop.sock:ro"
            echo -e "${GREEN}   ✓ jtop socket found: /run/jtop.sock${NC}"
        else
            echo -e "${YELLOW}   ⚠️  jtop socket not found: /run/jtop.sock${NC}"
            echo -e "${YELLOW}      GPU monitoring may not work properly${NC}"
            echo -e "${YELLOW}      Start jtop service: sudo systemctl start jtop${NC}"
            echo ""
            read -p "   Continue anyway? (y/N): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                echo -e "${YELLOW}Aborted. Start jtop service first.${NC}"
                exit 1
            fi
            # Still mount it (might be created later or container will handle gracefully)
            DOCKER_CMD="$DOCKER_CMD -v /run/jtop.sock:/run/jtop.sock:ro"
        fi
    fi

    # Add image name
    DOCKER_CMD="$DOCKER_CMD ${IMAGE_NAME}"
fi

# Show the full command before executing (for debugging)
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}🐳 Docker Run Command:${NC}"
echo -e "${YELLOW}${DOCKER_CMD}${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Execute
eval $DOCKER_CMD

# Wait a moment for container to start
sleep 2

# Check if container is running
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${GREEN}✅ Container started successfully!${NC}"

    # Verify jtop socket mount for Jetson platforms
    if [[ "$PLATFORM" == "jetson-"* ]]; then
        echo ""
        echo -e "${BLUE}🔍 Verifying jtop socket mount...${NC}"
        if docker exec ${CONTAINER_NAME} test -S /run/jtop.sock 2>/dev/null; then
            echo -e "${GREEN}   ✓ jtop socket mounted correctly in container${NC}"
        else
            echo -e "${YELLOW}   ⚠️  jtop socket not accessible in container${NC}"
            echo -e "${YELLOW}      GPU monitoring may not work${NC}"
            echo -e "${YELLOW}      Check: docker exec ${CONTAINER_NAME} ls -l /run/jtop.sock${NC}"
        fi
    fi

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
