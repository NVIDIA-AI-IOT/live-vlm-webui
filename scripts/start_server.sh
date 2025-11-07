#!/bin/bash
# Start Live VLM WebUI Server with HTTPS

# Get script directory and navigate to project root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# Detect Jetson and recommend Docker
if [ -f /etc/nv_tegra_release ]; then
    echo "⚠️  Jetson platform detected!"
    echo ""
    echo "📦 We STRONGLY recommend using Docker for Jetson:"
    echo "   ./scripts/start_container.sh"
    echo ""
    echo "Why Docker?"
    echo "  ✅ No system package dependencies"
    echo "  ✅ Works out-of-the-box"
    echo "  ✅ Production-ready"
    echo "  ✅ Isolated from JetPack"
    echo ""
    echo "Local Python on Jetson requires:"
    echo "  • sudo apt install python3-venv (or python3.10-venv)"
    echo "  • pip upgrade to support modern packaging"
    echo "  • May conflict with JetPack packages"
    echo ""
    read -p "Continue with local Python anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "👍 Good choice! Run: ./scripts/start_container.sh"
        exit 0
    fi
    echo "⚠️  Proceeding with local Python setup..."
    echo ""
fi

# Detect and activate virtual environment if needed
DETECTED_VENV=""
if [ -z "$VIRTUAL_ENV" ] && [ -z "$CONDA_DEFAULT_ENV" ]; then
    # Check for .venv (preferred)
    if [ -d ".venv" ]; then
        echo "Activating .venv virtual environment..."
        source .venv/bin/activate
        DETECTED_VENV=".venv"
        echo ""
    # Check for venv (alternative)
    elif [ -d "venv" ]; then
        echo "Activating venv virtual environment..."
        source venv/bin/activate
        DETECTED_VENV="venv"
        echo ""
    else
        echo "⚠️  No virtual environment detected!"
        echo "Please create one first:"
        echo "  python3 -m venv .venv"
        echo "  source .venv/bin/activate"
        echo "  pip install -e ."
        echo ""
        echo "Or activate your conda environment:"
        echo "  conda activate live-vlm-webui"
        exit 1
    fi
fi

# Check if the package is installed in the current environment
if ! python -c "import live_vlm_webui" 2>/dev/null; then
    echo "❌ Error: live_vlm_webui package not found!"
    echo ""

    # Detect which environment tool is available (prioritize venv over conda)
    if [ -n "$VIRTUAL_ENV" ]; then
        ENV_TYPE="virtual environment '$(basename $VIRTUAL_ENV)'"
    elif [ -n "$CONDA_DEFAULT_ENV" ]; then
        ENV_TYPE="conda environment '$CONDA_DEFAULT_ENV'"
    else
        ENV_TYPE="current environment"
    fi

    echo "You are in $ENV_TYPE but the package is not installed."
    echo ""
    echo "📋 To fix this, run ONE of the following:"
    echo ""

    # Show the venv that was actually detected/activated
    if [ -n "$DETECTED_VENV" ]; then
        echo "Option 1: Install in the detected virtual environment"
        echo "  source $DETECTED_VENV/bin/activate"
        echo "  pip install --upgrade pip setuptools wheel"
        echo "  pip install -e ."
        echo ""
    elif [ -d ".venv" ] || [ -d "venv" ]; then
        # Fallback if we're already in a venv but didn't detect it
        VENV_DIR=$([ -d ".venv" ] && echo ".venv" || echo "venv")
        echo "Option 1: Use the project's virtual environment"
        echo "  source $VENV_DIR/bin/activate"
        echo "  pip install --upgrade pip setuptools wheel"
        echo "  pip install -e ."
        echo ""
    fi

    # Conda option
    if command -v conda &> /dev/null; then
        echo "Option 2: Install in conda environment"
        echo "  conda activate $CONDA_DEFAULT_ENV"
        echo "  pip install -e ."
        echo ""
    fi

    # Generic pip install
    echo "Option 3: Install in current environment"
    echo "  pip install --upgrade pip setuptools wheel"
    echo "  pip install -e ."
    echo ""

    echo "💡 Tips:"
    echo "   - Upgrade pip first if you get 'setup.py not found' errors"
    echo "   - 'pip install -e .' installs in editable mode (changes take effect immediately)"
    echo ""
    exit 1
fi

# Check if certificates exist
if [ ! -f "cert.pem" ] || [ ! -f "key.pem" ]; then
    echo "Certificates not found. Generating..."
    ./scripts/generate_cert.sh
    echo ""
fi

# Check if port 8090 is already in use
PORT_IN_USE=false

# Method 1: Try to bind to the port (most reliable)
if python -c "import socket; s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('0.0.0.0', 8090)); s.close()" 2>/dev/null; then
    PORT_IN_USE=false
else
    PORT_IN_USE=true
fi

# Method 2: Check for Docker containers (if method 1 says port is in use)
DOCKER_CONTAINER=""
if [ "$PORT_IN_USE" = true ] && command -v docker &> /dev/null; then
    DOCKER_CONTAINER=$(docker ps --filter "name=live-vlm-webui" --format "{{.Names}}" 2>/dev/null | head -1)
fi

if [ "$PORT_IN_USE" = true ]; then
    echo "❌ Error: Port 8090 is already in use!"
    echo ""

    if [ -n "$DOCKER_CONTAINER" ]; then
        echo "🐳 Found Docker container: $DOCKER_CONTAINER"
        echo ""
        echo "📋 To fix this, stop the Docker container:"
        echo "  docker stop $DOCKER_CONTAINER"
        echo ""
    else
        echo "This could be:"
        echo "  • Another instance of this server running"
        echo "  • A Docker container running the WebUI"
        echo "  • Another application using port 8090"
        echo ""
        echo "📋 To fix this:"
        echo ""
        echo "Option 1: Check Docker containers"
        echo "  docker ps  # Check running containers"
        echo "  docker stop live-vlm-webui  # Stop if found"
        echo ""
        echo "Option 2: Find and kill the process"
        if command -v lsof &> /dev/null; then
            PID=$(lsof -ti :8090 2>/dev/null | head -1)
            if [ -n "$PID" ]; then
                PROC_INFO=$(ps -p $PID -o comm= 2>/dev/null || echo "unknown")
                echo "  Process using port 8090: PID $PID ($PROC_INFO)"
                echo "  kill $PID"
            else
                echo "  lsof -ti :8090  # Find the process"
                echo "  kill <PID>      # Stop it"
            fi
        else
            echo "  netstat -tulpn | grep :8090  # Find the process"
            echo "  kill <PID>                    # Stop it"
        fi
        echo ""
        echo "Option 3: Use a different port"
        echo "  ./scripts/start_server.sh --port 8091"
        echo ""
    fi
    exit 1
fi

# Start server with HTTPS
echo "Starting Live VLM WebUI server..."
echo "Auto-detecting local VLM services (Ollama, vLLM, SGLang)..."
echo "Will fall back to NVIDIA API Catalog if none found"
echo ""
echo "⚠️  Your browser will show a security warning (self-signed certificate)"
echo "    Click 'Advanced' → 'Proceed to localhost' (or 'Accept Risk')"
echo ""

# Run server with auto-detection (no --model or --api-base specified)
# To override, use: ./scripts/start_server.sh --model YOUR_MODEL --api-base YOUR_API
python -m live_vlm_webui.server \
  --ssl-cert cert.pem \
  --ssl-key key.pem \
  --host 0.0.0.0 \
  --port 8090 \
  "$@"

