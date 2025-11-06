# Live VLM WebUI

**A universal web interface for real-time Vision Language Model interaction and benchmarking.**

Stream your webcam to any VLM and get live AI-powered analysis - perfect for testing models, benchmarking performance, and exploring vision AI capabilities across different hardware platforms.

## Key Highlights

🌐 **Universal Compatibility** - Works with **any VLM served via OpenAI-compatible API** using base64-encoded images. Deploy on:
- 🟢 **NVIDIA Jetson** (Orin, AGX Xavier)
- 🔵 **NVIDIA DGX Spark** systems
- 🖥️ **Desktop/Workstation** (Linux, potentially Mac)
- ☁️ **Cloud APIs** (OpenAI, Anthropic, etc.)

## Screenshot

![](./docs/images/chrome_app_running.png)

---

## 🚀 Quick Start (The Easy Way)

**Use Docker - same method for PC, Jetson Orin, and Jetson Thor!**

```bash
# Clone the repo
git clone https://github.com/nvidia-ai-iot/live-vlm-webui.git
cd live-vlm-webui

# Run the auto-detection script
./start_container.sh
```

That's it! The script will:
- ✅ Auto-detect your platform (PC x86_64, Jetson Orin, or Jetson Thor)
- ✅ Pull the appropriate pre-built image from GitHub Container Registry
- ✅ Configure GPU access automatically
- ✅ Start the container with correct settings

Access the web UI at: **https://localhost:8090**

> 💡 **Note:** You'll need a VLM backend running (Ollama, vLLM, etc.). See [VLM Backend Setup](#option-a-ollama-easiest) below.

### Available Pre-built Images

| Platform | Image Tag | Pull Command |
|----------|-----------|--------------|
| **PC (x86_64)** | `latest-x86` | `docker pull ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-x86` |
| **Jetson Orin** | `latest-jetson-orin` | `docker pull ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-jetson-orin` |
| **Jetson Thor** | `latest-jetson-thor` | `docker pull ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-jetson-thor` |

---

## Features

### Core Functionality
- 🎥 **Real-time WebRTC streaming** - Low-latency bidirectional video
- 🔌 **OpenAI-compatible API** - Works with vLLM, SGLang, Ollama, TGI, or any vision API endpoint that uses base64-encoded images
- 📝 **Interactive prompt editor** - 10+ preset prompts (scene description, object detection, safety monitoring, OCR, etc.) + custom prompts
- ⚡ **Async processing** - Smooth video while VLM processes frames in background
- 🔧 **Flexible deployment** - Local inference or cloud APIs (OpenAI, Anthropic, etc.)

### UI & Visualization
- 🎨 **Modern NVIDIA-themed UI** - Professional design inspired by NVIDIA NGC Catalog with NVIDIA green accents
- 🌓 **Light/Dark theme toggle** - Automatic preference persistence with localStorage
- 📊 **Live system monitoring** - Real-time GPU, VRAM, CPU, and RAM stats with sparkline charts
- ⏱️ **Inference metrics** - Live latency tracking (last, average, total count) displayed with VLM output
- 🪞 **Video mirroring** - Toggle button overlay on camera view
- 📱 **Compact layout** - Single-screen design with all content visible (video, output, stats, controls)

### Configuration & Control
- 🎛️ **Dynamic settings** - Change model, prompt, and processing interval without restarting
- 📹 **Multi-camera support** - Detect and switch between multiple cameras (USB webcams, built-in cameras)
- 🔄 **Model auto-detection** - Refresh button to discover available models from API
- ⚙️ **Adjustable processing rate** - Control frame interval (1-3600 frames, default 30)
- 🎯 **Max tokens control** - Fine-tune output length (1-4096 tokens)
- 🔌 **WebSocket real-time updates** - Instant feedback on settings and analysis results

### Platform Support
- 💻 **Cross-platform monitoring** - Auto-detects NVIDIA GPUs (NVML), with framework for Apple Silicon and AMD
- 🖥️ **Dynamic system detection** - Automatically displays CPU model name and hostname
  - Linux: reads from `/proc/cpuinfo`
  - macOS: uses `sysctl machdep.cpu.brand_string`
  - Windows: uses WMIC
- 🔒 **HTTPS support** - Self-signed certificates for secure webcam access

## Future Enhancements (Roadmap)

- [ ] Benchmark mode for side-by-side model comparison
- [ ] Model download UI (➕ button placeholder ready)
- [ ] Cloud API templates (OpenAI, Anthropic quick configs)
- [ ] Recording functionality (save analysis sessions)
- [ ] Export results (JSON, CSV)
- [ ] Mobile app support
- [ ] **Hardware-accelerated video processing on Jetson** - Use NVENC/NVDEC for color space conversion instead of CPU-based swscaler (current implementation uses software conversion which is not optimal for Jetson platforms)

## Architecture

1. **Uplink**: Webcam video → WebRTC → Server
2. **Processing**: Server extracts frames → VLM analyzes based on your prompt (async)
3. **Downlink**: Clean video stream → WebRTC → Browser
4. **UI Updates**: VLM results → WebSocket → Real-time text display

The VLM processes frames asynchronously in the background. The video stream continues smoothly while results are displayed in a separate text panel via WebSocket, ensuring no visual interference and instant updates when new analysis completes.

## Prerequisites

- Python 3.8+
- A VLM serving backend (choose one):
  - [vLLM](https://github.com/vllm-project/vllm) (recommended for performance)
  - [SGLang](https://github.com/sgl-project/sglang) (good for complex reasoning)
  - [Ollama](https://ollama.ai/) (easiest to get started)
  - Any OpenAI-compatible API
- Webcam (V4L2 compliant video source)

## 🐋 Docker Deployment (Advanced)

### Manual Docker Run

If you prefer manual control over the Docker container:

**PC (x86_64):**
```bash
docker run -d \
  --name live-vlm-webui \
  --network host \
  --gpus all \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-x86
```

**Jetson Orin:**
```bash
docker run -d \
  --name live-vlm-webui \
  --network host \
  --runtime nvidia \
  --privileged \
  -v /run/jtop.sock:/run/jtop.sock:ro \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-jetson-orin
```

**Jetson Thor:**
```bash
docker run -d \
  --name live-vlm-webui \
  --network host \
  --gpus all \
  --privileged \
  -v /run/jtop.sock:/run/jtop.sock:ro \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-jetson-thor
```

Then access the web UI at `https://localhost:8090`

---

## Manual Installation (Alternative to Docker)

For those who prefer native Python installation or need to customize the setup:

### 1. Clone the repository
```bash
git clone https://github.com/nvidia-ai-iot/live-vlm-webui.git
cd live-vlm-webui
```

### 2. Create a virtual environment

**Option A: venv (recommended for Jetson/lightweight systems):**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Option B: conda (recommended for desktop/workstation):**
```bash
conda create -n live-vlm-webui python=3.10 -y
conda activate live-vlm-webui
```

### 3. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 4. Generate SSL certificates (required for webcam access)
```bash
./generate_cert.sh
```

This will create `cert.pem` and `key.pem` in the project directory. These are self-signed certificates for local development.

> **Note:** Modern browsers require HTTPS to access webcam/microphone. The self-signed certificate will trigger a security warning - you'll need to click "Advanced" → "Proceed" to accept it.

### 5. Platform-Specific Notes

**Jetson (ARM64):**
- Use `venv` instead of conda (lighter footprint)
- Recommended for Jetson Orin, AGX Xavier, Nano
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Desktop/Workstation (x86_64):**
- Either `venv` or `conda` works well
- `conda` better for data science workflows
- `venv` faster and more lightweight

**Why `.venv` (with dot)?**
- Modern Python standard (PEP 405)
- Auto-detected by VS Code, PyCharm
- Keeps directory clean (hidden by default)
- Already in `.gitignore`

### 6. Set up your VLM backend (choose one)

### Option A: Ollama (Easiest)
```bash
# Install ollama from https://ollama.ai/download
# Pull a vision model
ollama pull llava:7b

# Start ollama server
ollama serve
```

### Option B: vLLM (Recommended)
```bash
# Install vLLM
pip install vllm

# Start vLLM server with a vision model
python -m vllm.entrypoints.openai.api_server \
  --model llama-3.2-11b-vision-instruct \
  --port 8000
```

### Option B2: vLLM on Jetson Thor (Container)

For NVIDIA Jetson Thor, use the official vLLM container from NGC:

```bash
# Pull and run vLLM container with model cache mounted
docker run --rm -it \
  --network host \
  --shm-size=16g \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  --runtime=nvidia \
  --name=vllm \
  -v $HOME/data/models/huggingface:/root/.cache/huggingface \
  -v $HOME/data/vllm_cache:/root/.cache/vllm \
  nvcr.io/nvidia/vllm:25.10-py3

# Inside the container, serve a vision model:
vllm serve microsoft/Phi-3.5-vision-instruct --trust-remote-code
```

**Notes:**
- Model files are cached in `$HOME/data/models/huggingface` for reuse
- First run will download the model (may take some time depending on model size)
- vLLM will be accessible at `http://localhost:8000` (default port)
- Use `--port` flag to change the port if needed
- For Phi-3.5-vision, approximately 8GB VRAM required

**Other recommended vision models for Jetson Thor:**
```bash
# Smaller, faster option (4B parameters)
vllm serve microsoft/Phi-3.5-vision-instruct --trust-remote-code

# Llama 3.2 Vision (11B parameters, higher quality)
vllm serve meta-llama/Llama-3.2-11B-Vision-Instruct --trust-remote-code

# Qwen2-VL (7B parameters, good balance)
vllm serve Qwen/Qwen2-VL-7B-Instruct --trust-remote-code
```

### Option C: SGLang
```bash
# Install SGLang
pip install "sglang[all]"

# Start SGLang server
python -m sglang.launch_server \
  --model-path llama-3.2-11b-vision-instruct \
  --port 30000
```

### Option D: NVIDIA API Catalog (Cloud)

Use NVIDIA's hosted API for instant access to vision models without local setup:

**1. Get your API Key:**
- Visit [NVIDIA API Catalog](https://build.nvidia.com/)
- Sign in with your NVIDIA account (free)
- Navigate to a vision model (e.g., [Llama 3.2 Vision](https://build.nvidia.com/meta/llama-3.2-90b-vision-instruct))
- Click "Get API Key" to generate your key

**2. Test with curl:**
```bash
# Example: Query Llama 3.2 Vision with an image
curl -X POST "https://ai.api.nvidia.com/v1/gr/meta/llama-3.2-90b-vision-instruct/chat/completions" \
  -H "Authorization: Bearer nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxXYZ" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta/llama-3.2-90b-vision-instruct",
    "messages": [
      {
        "role": "user",
        "content": "What is in this image? <img src=\"data:image/png;base64,iVBORw0K...\" />"
      }
    ],
    "max_tokens": 512
  }'
```

**3. Use with live-vlm-webui:**

No server setup needed! Just configure via the web UI:
- **API Base URL**: `https://ai.api.nvidia.com/v1/gr`
- **API Key**: `nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxXYZ` (your actual key)
- **Model**: Select from available vision models (e.g., `meta/llama-3.2-90b-vision-instruct`)

The WebUI will auto-detect NVIDIA API Catalog and show/hide the API Key field accordingly.

**Available Vision Models:**
- `meta/llama-3.2-90b-vision-instruct` - Highest quality, 90B parameters
- `meta/llama-3.2-11b-vision-instruct` - Good balance, 11B parameters
- `microsoft/phi-3-vision-128k-instruct` - Fast and efficient, 4.2B parameters
- `nvidia/neva-22b` - NVIDIA's vision model, 22B parameters

**Notes:**
- Free tier available with rate limits
- No local GPU required
- Lowest latency for users without local VLM infrastructure
- API key format: `nvapi-` followed by ~60 alphanumeric characters
- Keep your API key secure - don't commit to git!

## Docker Deployment

### Option 1: Docker Compose (Recommended - Full Stack)

Run the entire stack (live-vlm-webui + Ollama) with one command:

```bash
# Start the services
docker-compose up -d

# Pull a vision model in Ollama
docker exec -it ollama ollama pull llama3.2-vision:11b

# View logs
docker-compose logs -f live-vlm-webui

# Stop the services
docker-compose down
```

**For NVIDIA GPU support**, you need to:

1. **Install NVIDIA Container Toolkit** (one-time setup):
   ```bash
   # Ubuntu/Debian
   distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
   curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
   curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
     sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
     sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

   sudo apt-get update
   sudo apt-get install -y nvidia-container-toolkit

   # Configure Docker to use NVIDIA runtime
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker

   # Verify GPU access works
   docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
   ```

   > **Note:** If `--gpus all` doesn't work, you may need to enable CDI:
   > ```bash
   > sudo mkdir -p /etc/cdi
   > sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
   > sudo nvidia-ctk runtime configure --runtime=docker --cdi.enabled
   > sudo systemctl restart docker
   > # Then use: --device nvidia.com/gpu=all instead of --gpus all
   > ```

2. **Enable GPU in docker-compose.yml** (uncomment GPU sections):
   ```yaml
   # For live-vlm-webui service (enables GPU monitoring in system stats)
   live-vlm-webui:
     deploy:
       resources:
         reservations:
           devices:
             - driver: nvidia
               count: 1
               capabilities: [gpu, utility]

   # For ollama service (enables model inference on GPU)
   ollama:
     deploy:
       resources:
         reservations:
           devices:
             - driver: nvidia
               count: 1
               capabilities: [gpu]
   ```

3. **Restart the stack**:
   ```bash
   docker-compose down
   docker-compose up -d
   ```

**Access the UI:**
- Open `https://localhost:8090` in your browser
- Accept the self-signed certificate warning
- System info should now show your actual GPU name!

### Option 2: Standalone Docker Container

If you already have a VLM backend running (Ollama, vLLM, etc.):

```bash
# Build the image
docker build -t live-vlm-webui .

# OPTION A: Connect to LOCAL services (Ollama/vLLM on the same host)
# Use --network host so container can access localhost services
docker run -d \
  --name live-vlm-webui \
  --network host \
  live-vlm-webui

# OPTION A with GPU monitoring (shows GPU name in system stats)
docker run -d \
  --name live-vlm-webui \
  --network host \
  --gpus all \
  live-vlm-webui

# The app will auto-detect Ollama on localhost:11434
# Access at: https://localhost:8090

# OPTION B: Connect to REMOTE VLM server or NVIDIA API Catalog
# Use -p port mapping when NOT using host network
docker run -d \
  --name live-vlm-webui \
  -p 8090:8090 \
  --gpus all \
  -e VLM_API_BASE=http://your-vlm-server:8000/v1 \
  -e VLM_MODEL=llama-3.2-11b-vision-instruct \
  live-vlm-webui
```

> **Important: Networking Explained**
> - **Use `--network host`** when connecting to services on the **same host** (e.g., Ollama on `localhost:11434`)
>   - With host network, the container shares your host's network stack
>   - Access the app directly at `https://localhost:8090`
>   - Port mapping (`-p`) is NOT needed with `--network host`
> - **Use `-p 8090:8090`** when connecting to **remote** services or cloud APIs
>   - This is standard Docker port mapping for isolated network
>
> **GPU Access:**
> - Add `--gpus all` to enable GPU monitoring (shows GPU name, utilization, VRAM)
> - Requires NVIDIA Container Toolkit installed (see above)
> - If `--gpus all` doesn't work, try `--device nvidia.com/gpu=all` (CDI format)
> - Without GPU access, system stats will show "N/A" for GPU name

### Option 3: Pre-built Images from GitHub Container Registry

We provide **platform-specific pre-built images**:

#### For x86_64 PC/Workstation:
```bash
# Pull the latest x86 image
docker pull ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-x86

# Run with local Ollama and GPU monitoring
docker run -d \
  --name live-vlm-webui \
  --network host \
  --gpus all \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-x86

# Or specify a version
docker pull ghcr.io/nvidia-ai-iot/live-vlm-webui:v1.0.0-x86
```

#### For NVIDIA Jetson (Orin, Thor):
```bash
# Pull the latest Jetson image
docker pull ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-jetson

# Run with local Ollama and GPU monitoring
docker run -d \
  --name live-vlm-webui \
  --network host \
  --runtime nvidia \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-jetson

# Or specify a version
docker pull ghcr.io/nvidia-ai-iot/live-vlm-webui:v1.0.0-jetson
```

**Image Tags:**
- `:latest-x86` - Latest x86_64 build (CUDA runtime, NVML support)
- `:latest-jetson` - Latest ARM64 Jetson build (L4T base, jtop/sysfs support)
- `:v1.0.0-x86` - Version-tagged x86_64 release
- `:v1.0.0-jetson` - Version-tagged Jetson release

### Custom SSL Certificates (Production)

For production deployment with your own SSL certificates:

```bash
# Generate your certificates (or use Let's Encrypt)
# Then mount them into the container:
docker run -d \
  --name live-vlm-webui \
  -p 8090:8090 \
  -v /path/to/your/cert.pem:/app/cert.pem:ro \
  -v /path/to/your/key.pem:/app/key.pem:ro \
  live-vlm-webui
```

Or using docker-compose (uncomment volume mounts in `docker-compose.yml`):
```yaml
volumes:
  - ./your-cert.pem:/app/cert.pem:ro
  - ./your-key.pem:/app/key.pem:ro
```

### Docker Image Details

We provide **platform-specific Dockerfiles** for optimal compatibility:

#### `Dockerfile` - For x86_64 PC/Workstation
**Base Image:** `nvidia/cuda:12.4.1-runtime-ubuntu22.04`
- Includes NVIDIA CUDA runtime libraries for GPU monitoring via NVML
- Enables `pynvml` to query GPU name, utilization, VRAM, temperature, and power
- Compatible with NVIDIA drivers 545+ (GeForce, Quadro, Tesla, etc.)
- Image size: ~1.5GB (compressed)

**Build:**
```bash
docker build -t live-vlm-webui:x86 .
```

#### `Dockerfile.jetson` & `Dockerfile.jetson-orin` - For NVIDIA Jetson Orin
**Base Image:** `nvcr.io/nvidia/l4t-base:r36.2.0` (L4T r36.2.0, JetPack 6.0)
- Optimized for Jetson Orin platform (AGX Orin, Orin Nano, Orin NX)
- Uses `jtop` (jetson-stats from PyPI) for GPU monitoring
- Supports JetPack 6.x
- Image size: ~1.2GB (compressed)

**Build:**
```bash
docker build -f Dockerfile.jetson -t live-vlm-webui:jetson-orin .
# Or explicitly:
docker build -f Dockerfile.jetson-orin -t live-vlm-webui:jetson-orin .
```

#### `Dockerfile.jetson-thor` - For NVIDIA Jetson Thor ✅ READY
**Base Image:** `nvcr.io/nvidia/cuda:13.0.0-runtime-ubuntu24.04` (Standard ARM64 CUDA container)
- **✅ Jetson Thor is SBSA-compliant** - Uses standard NGC CUDA containers (no L4T-specific images needed!)
- This is a major architectural change from previous Jetsons (Orin, Xavier)
- Uses `jtop` (jetson-stats from GitHub) for latest Thor GPU monitoring support
- Installs from GitHub: `git+https://github.com/rbonghi/jetson_stats.git`
- Ubuntu 24.04 base (aligned with JetPack 7.x)
- Reference: [Jetson Thor CUDA Setup Guide](https://docs.nvidia.com/jetson/agx-thor-devkit/user-guide/latest/setup_cuda.html)

**Build:**
```bash
docker build -f Dockerfile.jetson-thor -t live-vlm-webui:jetson-thor .
```

**Run:**
```bash
docker run -d \
  --name live-vlm-webui \
  --network host \
  --gpus all \
  --privileged \
  -v /run/jtop.sock:/run/jtop.sock:ro \
  live-vlm-webui:jetson-thor
```

**Why separate Dockerfiles?**
- **Jetson Orin**: Requires L4T-specific base images (`l4t-base:r36.x`)
- **Jetson Thor**: SBSA-compliant, uses standard CUDA containers (architectural upgrade!)
- **Monitoring**: Both use `jtop` for GPU stats (NVML limited on Jetson)
- **jetson-stats source**: Orin uses PyPI (stable), Thor uses GitHub (bleeding-edge support)

**Without GPU access** (`--gpus all` or `--device nvidia.com/gpu=all`), the app still works but system stats show "N/A" for GPU info.

### Building Platform-Specific Images (For Developers)

If you want to build and publish your own images:

**1. Authenticate with GitHub Container Registry:**
```bash
# Create a Personal Access Token with 'write:packages' scope at https://github.com/settings/tokens
echo $GITHUB_TOKEN | docker login ghcr.io -u YOUR_USERNAME --password-stdin
```

**2. Build and push x86_64 image:**
```bash
# Build locally
docker build -t live-vlm-webui:x86 .

# Tag for GHCR
docker tag live-vlm-webui:x86 ghcr.io/YOUR_USERNAME/live-vlm-webui:latest-x86
docker tag live-vlm-webui:x86 ghcr.io/YOUR_USERNAME/live-vlm-webui:v1.0.0-x86

# Push
docker push ghcr.io/YOUR_USERNAME/live-vlm-webui:latest-x86
docker push ghcr.io/YOUR_USERNAME/live-vlm-webui:v1.0.0-x86
```

**3. Build and push Jetson image (ARM64):**
```bash
# On Jetson device or using QEMU
docker build -f Dockerfile.jetson -t live-vlm-webui:jetson .

# Tag for GHCR
docker tag live-vlm-webui:jetson ghcr.io/YOUR_USERNAME/live-vlm-webui:latest-jetson
docker tag live-vlm-webui:jetson ghcr.io/YOUR_USERNAME/live-vlm-webui:v1.0.0-jetson

# Push
docker push ghcr.io/YOUR_USERNAME/live-vlm-webui:latest-jetson
docker push ghcr.io/YOUR_USERNAME/live-vlm-webui:v1.0.0-jetson
```

**4. Or use GitHub Actions** (recommended):
- Push to `main` branch → Automatically builds both x86 and Jetson images
- Create a git tag like `v1.0.0` → Builds versioned releases
- See `.github/workflows/docker-publish.yml` for the workflow

### Environment Variables

Configure the application using environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `VLM_API_BASE` | Auto-detected | VLM API endpoint URL |
| `VLM_MODEL` | Auto-detected | Model name to use |
| `VLM_PROMPT` | "Describe..." | Default prompt |
| `VLM_API_KEY` | - | API key (for cloud services) |
| `PORT` | 8090 | Server port |

## Usage

### Quick Start

1. **Start your VLM backend** (see installation above)

2. **Start the server** (easiest way):
```bash
./start_server.sh
```

This will automatically start the server with SSL enabled using Ollama's `llama3.2-vision:11b` model.

3. **Open your browser** and navigate to:
```
https://<IP_ADDRESS>:8090
```

4. **Accept the security warning** (click "Advanced" → "Proceed")

Click "**Advanced**" button.

![](./docs/images/chrome_advanced.png)

Then click on "**Proceeed to <IP_ADDRESS> (unsafe)**".

![](./docs/images/chrome_proceed.png)

5. **Click "OPEN CAMERA AND START VLM ANALYSIS"** and allow camera access

![](./docs/images/chrome_webcam_access.png)

### Using the Web Interface

Once the server is running, the web interface provides full control:

**Left Sidebar Controls:**
- **VLM API Configuration** - Change API URL, key, and model on-the-fly
  - **Model Selection** with compact icon buttons: 🔄 Refresh models | ➕ Download (coming soon)
  - Auto-detects available models from your VLM API
- **Camera and App Control** - Select camera and control application
  - Dropdown menu lists all detected video input devices
  - Switch cameras on-the-fly without restarting analysis
  - **Start/Stop buttons** - Open camera and start VLM analysis | Stop
- **Prompt Editor** - Choose from 10+ presets or write custom prompts
  - Adjust **Max Tokens** for response length (1-4096)
  - Presets include: scene description, object detection, safety monitoring, OCR, emotion detection, etc.
- **Processing Settings** - Set frame interval (1-3600 frames)
  - Lower (5-30) = more frequent analysis, higher GPU usage
  - Higher (60-300) = less frequent, good for benchmarking and power saving

**Main Content Area:**
- **Video Feed** - Live webcam with mirror toggle button (🔄) overlay in corner
- **VLM Output Card** - Real-time analysis results with:
  - Model name and inference latency metrics (last, average, count)
  - Current prompt display (shown in gray box with green accent)
  - Generated text output with fade effect to indicate freshness
- **System Stats Card** - Live monitoring with:
  - System info header: `hostname (CPU model) with GPU name`
  - GPU utilization and VRAM usage with progress bars
  - CPU and RAM stats with progress bars
  - Sparkline graphs showing historical trends (60-second window)

**Header:**
- **Logo and Title** - 🎥 "Live VLM WebUI" with subtitle
- **Connection Status** - Shows WebSocket connectivity (Connected/Disconnected)
- **Theme Toggle** - Switch between Light/Dark modes (🌙/☀️)

### Manual Usage

**With vLLM**:
```bash
python server.py --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem
```

**With SGLang**:
```bash
python server.py --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:30000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem
```

**With Ollama**:
```bash
python server.py --model llava:7b \
  --api-base http://localhost:11434/v1 \
  --ssl-cert cert.pem --ssl-key key.pem
```

### Custom Prompts - Beyond Captioning

The real power is in custom prompts! Here are some examples:

**Scene Description (default)**:
```bash
python server.py --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "Describe what you see in this image in one sentence."
```

**Object Detection**:
```bash
python server.py --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "List all objects you can see in this image."
```

**Safety Monitoring**:
```bash
python server.py --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "Alert me if you see any safety hazards or dangerous situations."
```

**Activity Recognition**:
```bash
python server.py --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "What activity is the person performing?"
```

**Accessibility**:
```bash
python server.py --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "Describe the scene in detail for a visually impaired person."
```

**Emotion/Expression Detection**:
```bash
python server.py --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "Describe the facial expressions and emotions you observe."
```

## Configuration

### Command-line Options

```bash
python server.py --help
```

**Required**:
- `--model MODEL` - VLM model name (e.g., `llama-3.2-11b-vision-instruct`)

**Optional**:
- `--host HOST` - Host to bind to (default: `0.0.0.0`)
- `--port PORT` - Port to bind to (default: `8090`)
- `--api-base URL` - VLM API base URL (default: `http://localhost:8000/v1`)
- `--api-key KEY` - API key, use `EMPTY` for local servers (default: `EMPTY`)
- `--prompt TEXT` - Custom prompt for VLM (default: scene description)
- `--process-every N` - Process every Nth frame (default: `30`)

### Example Configurations

**High-frequency updates** (more responsive, higher CPU usage):
```bash
python server.py --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --process-every 15
```

**Custom port and host**:
```bash
python server.py --model llava:7b \
  --api-base http://localhost:11434/v1 \
  --host 0.0.0.0 \
  --port 3000
```

**Using OpenAI API** (or any remote service):
```bash
python server.py --model gpt-4-vision-preview \
  --api-base https://api.openai.com/v1 \
  --api-key your-api-key-here
```

## Performance Tuning

### Frame Processing Rate
You can adjust frame processing in two ways:

**Via Command Line** (at startup):
```bash
python server.py --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --process-every 60  # Process every 60 frames
```

**Via Web UI** (while running):
- Go to "Processing Settings" in the left sidebar
- Change "Frame Processing Interval" (1-3600 frames)
- Click "Apply Settings" - takes effect immediately!

**Guidelines:**
- **Lower values** (5-15 frames) = more frequent analysis, higher GPU usage (~2-6 FPS @ 30fps)
- **Default** (30 frames) = balanced, ~1 FPS analysis @ 30fps video
- **Higher values** (60-120 frames) = less frequent, good for monitoring (~0.25-0.5 FPS)
- **Very high** (300-3600 frames) = infrequent updates for benchmarking or power saving
  - 300 frames = ~10 second intervals @ 30fps
  - 900 frames = ~30 second intervals @ 30fps
  - 3600 frames = ~2 minute intervals @ 30fps

### Model Selection
Choose based on your hardware and needs:

**Fast models (good for prototyping)**:
- `llava:7b` (Ollama)
- `llava-1.5-7b-hf` (vLLM/SGLang)

**Balanced**:
- `llama-3.2-11b-vision-instruct` (recommended)
- `llava:13b`

**High quality** (requires significant GPU memory):
- `llava:34b`
- `gpt-4-vision-preview` (via OpenAI API)

### Video Resolution
Edit `index.html` to change the requested video resolution:
```javascript
video: {
    width: { ideal: 640 },   // Lower for better performance
    height: { ideal: 480 }
}
```

## API Compatibility

This tool uses the OpenAI chat completions API format with vision support. Any backend that implements this standard will work:

### Tested Backends
- ✅ **vLLM** - Best performance, production-ready
- ✅ **SGLang** - Great for complex prompts
- ✅ **Ollama** - Easiest setup
- ✅ **OpenAI API** - Cloud-based (requires API key)

### Message Format
```python
{
  "model": "model-name",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "your prompt"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
    ]
  }]
}
```

## Use Cases

- 🎬 **Content Creation** - Live scene analysis for video production
- 🔒 **Security** - Real-time monitoring and alert generation
- ♿ **Accessibility** - Visual assistance for visually impaired users
- 🎮 **Gaming** - AI game master or interactive experiences
- 🏥 **Healthcare** - Activity monitoring, fall detection
- 🏭 **Industrial** - Quality control, safety monitoring
- 📚 **Education** - Interactive learning experiences
- 🤖 **Robotics** - Visual feedback for robot control

## Project Structure

```
live-vlm-webui/
├── server.py            # Main WebRTC server with WebSocket support
├── video_processor.py   # Video frame processing and VLM integration
├── vlm_service.py       # VLM service (OpenAI-compatible API client)
├── gpu_monitor.py       # Cross-platform GPU/system monitoring (NVML, etc.)
├── index.html           # Frontend web UI (NVIDIA-themed, dark/light mode)
├── requirements.txt     # Python dependencies
├── start_server.sh      # Quick start script with SSL
├── generate_cert.sh     # SSL certificate generation script
├── examples.sh          # Example commands for different setups
├── ROADMAP.md          # Detailed future plans and milestones
├── .gitignore           # Git ignore patterns
└── README.md           # This file
```

## Troubleshooting

### Camera not accessible

**Issue:** Browser won't allow camera access
**Solution:**
- ✅ Make sure you're using **HTTPS** (not HTTP)
- ✅ Generate SSL certificates: `./generate_cert.sh`
- ✅ Start server with SSL: `./start_server.sh` or add `--ssl-cert cert.pem --ssl-key key.pem`
- ✅ Accept the security warning in your browser (Advanced → Proceed)
- ✅ Check browser permissions for camera access
- ✅ Try Chrome/Edge (best WebRTC support)

**Important:** Modern browsers require HTTPS to access webcam/microphone for security reasons.

### SSL Certificate Warning

**Issue:** Browser shows "Your connection is not private" warning
**Solution:** This is normal for self-signed certificates!
1. Click **"Advanced"** or **"Show Details"**
2. Click **"Proceed to localhost (unsafe)"** or **"Accept the Risk and Continue"**
3. The warning appears because we're using a self-signed certificate for local development

For production use, get a proper SSL certificate from Let's Encrypt or a certificate authority.

### VLM connection errors
- Verify your VLM backend is running
- Check the API base URL matches your backend's port
- For vLLM: `http://localhost:8000/v1`
- For SGLang: `http://localhost:30000/v1`
- For Ollama: `http://localhost:11434/v1`

### "Model not found" errors
- Ensure the model is loaded in your backend
- Model names must match exactly
- For Ollama, use `ollama list` to see available models

### Slow performance
- Use a smaller/faster model (e.g., `llava:7b`)
- Increase `--process-every` to process fewer frames
- Reduce video resolution in `index.html`
- Ensure your VLM backend is using GPU acceleration

### Connection issues
- Check that the server is running and accessible
- Verify firewall settings if accessing from another device
- Try using `--host 0.0.0.0` to bind to all interfaces

## Development

### Customizing the VLM Service

Edit `vlm_service.py` to customize API calls:

```python
# Add custom parameters
response = await self.client.chat.completions.create(
    model=self.model,
    messages=messages,
    max_tokens=self.max_tokens,
    temperature=0.7,  # Adjust for creativity
    top_p=0.9,        # Adjust for diversity
)
```

### WebSocket Communication

The server uses WebSocket for real-time bidirectional communication:

**Server → Client:**
- `vlm_response` - VLM analysis results and metrics
- `gpu_stats` - System monitoring data (GPU, CPU, RAM)
- `status` - Connection and processing status updates

**Client → Server:**
- `update_prompt` - Change prompt and max_tokens on-the-fly
- `update_model` - Switch VLM model without restart
- `update_processing` - Adjust frame processing interval

Example: Sending a prompt update from JavaScript:
```javascript
websocket.send(JSON.stringify({
    type: 'update_prompt',
    prompt: 'Describe the scene',
    max_tokens: 100
}));
```

### Adding New GPU Monitors

Extend `gpu_monitor.py` for new platforms:

```python
class AppleSiliconMonitor(GPUMonitor):
    """Monitoring for Apple M1/M2/M3 chips"""

    def get_stats(self) -> Dict:
        # Use powermetrics or ioreg to get GPU stats
        # Return standardized dict format
        pass
```

### Customizing the UI Theme

Edit CSS variables in `index.html` to customize colors:

```css
:root {
    --nvidia-green: #76B900;  /* NVIDIA brand color */
    --bg-primary: #000000;    /* Dark theme background */
    --text-primary: #FFFFFF;  /* Text color */
    /* ... more variables */
}
```

## License

MIT License - Feel free to use and modify for your projects!

## Contributing

Contributions welcome! Areas for improvement:
- ✅ ~~WebSocket support for dynamic prompt updates~~ (Implemented!)
- ✅ ~~Live GPU/system monitoring~~ (Implemented!)
- ✅ ~~Interactive prompt editor~~ (Implemented!)
- ✅ ~~Inference latency metrics~~ (Implemented!)
- 🔄 Apple Silicon GPU monitoring
- 🔄 AMD GPU monitoring
- 📹 Recording functionality
- 🎥 Multiple simultaneous camera support
- 🔊 Audio description output (TTS)
- 📱 Mobile app support
- 🏆 Benchmark mode with side-by-side comparison
- 📊 Export analysis results (JSON, CSV)

## Acknowledgments

- Built with [aiortc](https://github.com/aiortc/aiortc) - Python WebRTC implementation
- Compatible with [vLLM](https://github.com/vllm-project/vllm), [SGLang](https://github.com/sgl-project/sglang), and [Ollama](https://ollama.ai/)
- Inspired by the growing ecosystem of open-source vision language models, including [NanoVLM](https://dusty-nv.github.io/NanoLLM/).

## Citation

If you use this in your research or project, please cite:

```bibtex
@software{live_vlm_webui,
  title = {Live VLM WebUI: Real-time Vision AI Interaction},
  year = {2025},
  url = {https://github.com/nvidia-ai-iot/live-vlm-webui}
}
```
