#!/bin/bash
# ==============================================================================
# Live VLM WebUI - Docker Compose Launcher
# ==============================================================================
# Automatically detects platform and launches the appropriate docker-compose
# profile with optional backend and model selection.
#
# Usage:
#   ./start_docker_compose.sh [backend] [model]
#
# Examples:
#   ./start_docker_compose.sh                    # Auto-detect platform, use Ollama
#   ./start_docker_compose.sh ollama             # Explicit Ollama
#   ./start_docker_compose.sh ollama llama3.2-vision:11b  # Ollama + specific model
#   ./start_docker_compose.sh vllm               # vLLM backend (future)
#   ./start_docker_compose.sh nim                # NVIDIA NIM with Cosmos-Reason1
# ==============================================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==============================================================================
# Check Prerequisites
# ==============================================================================
check_docker() {
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

    # Check if docker compose is available
    if ! docker compose version &> /dev/null; then
        # Try old docker-compose (with hyphen)
        if ! command -v docker-compose &> /dev/null; then
            echo -e "${RED}❌ Docker Compose not found!${NC}"
            echo ""
            echo -e "${YELLOW}Install Docker Compose:${NC}"
            echo -e "  ${BLUE}https://docs.docker.com/compose/install/${NC}"
            echo ""
            echo -e "Or use: ${GREEN}sudo apt install docker-compose${NC}"
            echo ""
            exit 1
        else
            echo -e "${YELLOW}⚠️  Using legacy docker-compose (V1)${NC}"
            echo -e "${YELLOW}   Recommend upgrading to V2: ${GREEN}sudo apt install docker-compose-plugin${NC}"
            echo ""
            DOCKER_COMPOSE_CMD="docker-compose"
        fi
    else
        DOCKER_COMPOSE_CMD="docker compose"
    fi

    echo -e "${GREEN}✅ Docker installed: $(docker --version)${NC}"
    echo -e "${GREEN}✅ Docker Compose: $DOCKER_COMPOSE_CMD${NC}"
    echo ""
}

check_docker

# Parse arguments
BACKEND="${1:-ollama}"  # Default to ollama
MODEL="${2:-}"          # Optional model (for Ollama) or NIM model variant

# ==============================================================================
# Platform Detection (same logic as start_container.sh)
# ==============================================================================
detect_platform() {
    local arch=$(uname -m)

    if [ "$arch" = "x86_64" ]; then
        echo "x86"
        return
    fi

    if [ "$arch" = "aarch64" ]; then
        # Check if it's a Jetson device
        if [ -f /etc/nv_tegra_release ]; then
            local jetson_info=$(cat /etc/nv_tegra_release)

            # Check for Orin
            if echo "$jetson_info" | grep -qi "orin"; then
                echo "jetson-orin"
                return
            fi

            # Check for Thor (might be in future L4T releases)
            if echo "$jetson_info" | grep -qi "thor"; then
                echo "jetson-thor"
                return
            fi

            # Generic Jetson (default to Orin for now)
            echo "jetson-orin"
            return
        fi

        # ARM64 but not Jetson (DGX Spark, ARM server)
        if command -v nvidia-smi &> /dev/null; then
            echo "x86"  # DGX Spark uses same profile as PC
            return
        fi

        # ARM64 without NVIDIA GPU
        echo "arm64"
        return
    fi

    echo "unknown"
}

PLATFORM=$(detect_platform)

# ==============================================================================
# Profile Selection
# ==============================================================================
select_profile() {
    local backend="$1"
    local platform="$2"

    case "$backend" in
        ollama)
            case "$platform" in
                x86)
                    echo "ollama"
                    ;;
                jetson-orin)
                    echo "ollama-jetson-orin"
                    ;;
                jetson-thor)
                    echo "ollama-jetson-thor"
                    ;;
                *)
                    echo "ollama"  # Default to standard
                    ;;
            esac
            ;;
        vllm)
            case "$platform" in
                x86)
                    echo "vllm"
                    ;;
                jetson-orin)
                    echo "vllm-jetson-orin"
                    ;;
                jetson-thor)
                    echo "vllm-jetson-thor"
                    ;;
                *)
                    echo "vllm"
                    ;;
            esac
            ;;
        nim)
            case "$platform" in
                x86)
                    echo "nim"
                    ;;
                jetson-orin)
                    echo "nim-jetson-orin"
                    ;;
                jetson-thor)
                    echo "nim-jetson-thor"
                    ;;
                *)
                    echo "nim"
                    ;;
            esac
            ;;
        *)
            echo -e "${RED}Error: Unknown backend '$backend'${NC}" >&2
            echo -e "Available backends: ollama, vllm, nim" >&2
            exit 1
            ;;
    esac
}

PROFILE=$(select_profile "$BACKEND" "$PLATFORM")

# ==============================================================================
# Compose File (Unified)
# ==============================================================================
COMPOSE_FILE="docker-compose.yml"  # Now unified for all backends!

# ==============================================================================
# Display Banner
# ==============================================================================
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Live VLM WebUI - Docker Compose Launcher${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${GREEN}🔍 Detected Platform:${NC} $PLATFORM"
echo -e "${GREEN}🐳 Backend:${NC} $BACKEND"
echo -e "${GREEN}📋 Profile:${NC} $PROFILE"
echo -e "${GREEN}📄 Compose File:${NC} $COMPOSE_FILE"
if [ -n "$MODEL" ]; then
    echo -e "${GREEN}🤖 Model:${NC} $MODEL"
fi
echo ""

# ==============================================================================
# Check for NGC API Key (if using NIM)
# ==============================================================================
if [ "$BACKEND" = "nim" ]; then
    if [ -z "$NGC_API_KEY" ]; then
        echo -e "${YELLOW}⚠️  Warning: NGC_API_KEY not set!${NC}"
        echo -e "NIM requires an NGC API key. Get yours at:"
        echo -e "  ${BLUE}https://org.ngc.nvidia.com/setup/api-key${NC}"
        echo ""
        read -p "Set NGC_API_KEY now? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            read -p "Enter NGC API Key: " NGC_API_KEY
            export NGC_API_KEY
        else
            echo -e "${RED}Cannot start NIM without NGC_API_KEY${NC}"
            exit 1
        fi
    fi
fi

# ==============================================================================
# Check for Existing Services
# ==============================================================================
check_existing_services() {
    local compose_file="$1"

    # Check if any containers from this compose file are running
    if $DOCKER_COMPOSE_CMD -f "$compose_file" ps -q 2>/dev/null | grep -q .; then
        echo -e "${YELLOW}⚠️  Existing services detected from $compose_file${NC}"
        echo ""
        $DOCKER_COMPOSE_CMD -f "$compose_file" ps
        echo ""
        read -p "Stop existing services? (Y/n): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            echo -e "${YELLOW}🛑 Stopping existing services...${NC}"
            $DOCKER_COMPOSE_CMD -f "$compose_file" down
            echo -e "${GREEN}✅ Existing services stopped${NC}"
            echo ""
        else
            echo -e "${RED}❌ Cannot start new services while old ones are running${NC}"
            exit 1
        fi
    fi
}

check_existing_services "$COMPOSE_FILE"

# ==============================================================================
# Start Services
# ==============================================================================
echo -e "${BLUE}🚀 Starting services...${NC}"
echo ""

# Build docker compose command
DOCKER_CMD="$DOCKER_COMPOSE_CMD -f $COMPOSE_FILE --profile $PROFILE up -d"

# Execute
echo -e "${BLUE}Running: ${NC}$DOCKER_CMD"
eval $DOCKER_CMD

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Failed to start services${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✅ Services started successfully!${NC}"

# ==============================================================================
# Pull/Download Model (if specified and backend is Ollama)
# ==============================================================================
if [ "$BACKEND" = "ollama" ] && [ -n "$MODEL" ]; then
    echo ""
    echo -e "${BLUE}🤖 Checking model: $MODEL${NC}"

    # Wait for Ollama to be ready
    echo -e "${YELLOW}⏳ Waiting for Ollama to be ready...${NC}"
    sleep 5

    # Check if model exists
    if docker exec ollama ollama list | grep -q "$MODEL"; then
        echo -e "${GREEN}✅ Model '$MODEL' already available${NC}"
    else
        echo -e "${YELLOW}📥 Pulling model '$MODEL'...${NC}"
        echo -e "${BLUE}This may take several minutes depending on model size${NC}"
        docker exec ollama ollama pull "$MODEL"

        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✅ Model '$MODEL' downloaded successfully${NC}"
        else
            echo -e "${RED}❌ Failed to download model '$MODEL'${NC}"
            echo -e "${YELLOW}You can manually pull it later with:${NC}"
            echo -e "  docker exec ollama ollama pull $MODEL"
        fi
    fi
fi

# ==============================================================================
# Display Access Information
# ==============================================================================
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🌐 Access Information${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

case "$BACKEND" in
    ollama)
        echo -e "${GREEN}Live VLM WebUI:${NC} https://localhost:8090"
        echo -e "${GREEN}Ollama API:${NC}     http://localhost:11434/v1"
        ;;
    vllm)
        echo -e "${GREEN}Live VLM WebUI:${NC} https://localhost:8090"
        echo -e "${GREEN}vLLM API:${NC}       http://localhost:8000/v1"
        ;;
    nim)
        echo -e "${GREEN}Live VLM WebUI:${NC} https://localhost:8090"
        echo -e "${GREEN}NIM API:${NC}        http://localhost:8000/v1"
        echo ""
        echo -e "${YELLOW}⚠️  First run: NIM will download ~10-15GB model (5-10 minutes)${NC}"
        echo -e "Monitor progress: ${BLUE}docker logs -f nim-cosmos-reason1-7b${NC}"
        ;;
esac

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# ==============================================================================
# Useful Commands
# ==============================================================================
echo -e "${BLUE}📝 Useful Commands:${NC}"
echo ""
echo -e "  View logs:        ${GREEN}docker compose -f $COMPOSE_FILE logs -f${NC}"
echo -e "  Stop services:    ${GREEN}docker compose -f $COMPOSE_FILE down${NC}"
echo -e "  List containers:  ${GREEN}docker compose -f $COMPOSE_FILE ps${NC}"

if [ "$BACKEND" = "ollama" ]; then
    echo -e "  List models:      ${GREEN}docker exec ollama ollama list${NC}"
    echo -e "  Pull model:       ${GREEN}docker exec ollama ollama pull <model>${NC}"
fi

echo ""
echo -e "${GREEN}🎉 Ready to use! Open https://localhost:8090 in your browser${NC}"
echo ""

