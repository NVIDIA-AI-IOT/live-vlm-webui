#!/bin/bash
# Stop Live VLM WebUI Docker Container

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

CONTAINER_NAME="live-vlm-webui"

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}    Stop Live-VLM-WebUI Docker Container${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker not found!${NC}"
    exit 1
fi

# Check if container exists
if ! docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${YELLOW}⚠️  Container '${CONTAINER_NAME}' not found${NC}"
    echo ""
    echo -e "Available containers:"
    docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" | grep -i vlm || echo "  (none)"
    exit 0
fi

# Check if container is running
IS_RUNNING=$(docker ps --format '{{.Names}}' | grep "^${CONTAINER_NAME}$" || true)

if [ -n "$IS_RUNNING" ]; then
    echo -e "${YELLOW}🛑 Stopping container '${CONTAINER_NAME}'...${NC}"
    docker stop ${CONTAINER_NAME}

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Container stopped${NC}"
    else
        echo -e "${RED}❌ Failed to stop container${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}⚠️  Container '${CONTAINER_NAME}' is not running${NC}"
fi

# Ask if user wants to remove the container
echo ""
read -p "Remove container? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}🗑️  Removing container '${CONTAINER_NAME}'...${NC}"
    docker rm ${CONTAINER_NAME}

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Container removed${NC}"
    else
        echo -e "${RED}❌ Failed to remove container${NC}"
        exit 1
    fi
else
    echo -e "${BLUE}ℹ️  Container stopped but not removed${NC}"
    echo -e "   To remove later: ${GREEN}docker rm ${CONTAINER_NAME}${NC}"
    echo -e "   To restart:      ${GREEN}docker start ${CONTAINER_NAME}${NC}"
fi

echo ""
echo -e "${GREEN}✨ Done!${NC}"

