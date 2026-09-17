# Docker Deployment Guide

Complete guide for deploying Live VLM WebUI using Docker on all supported platforms.

---

## 🚀 Quick Start (Recommended)

### Automatic Setup Script

The easiest way to run Live VLM WebUI in Docker:

```bash
./scripts/start_container.sh
```

**What the script does:**
- ✅ Auto-detects your platform (PC, Jetson Orin, Jetson Thor, Mac)
- ✅ Pulls the appropriate pre-built image from GitHub Container Registry
- ✅ Configures GPU access automatically
- ✅ Sets up correct runtime and permissions
- ✅ Starts the container with optimal settings

**Supported platforms:**
- x86_64 PC
- NVIDIA DGX Spark
- NVIDIA Jetson AGX Orin
- NVIDIA Jetson Orin Nano
- NVIDIA Jetson AGX Thor
- ⚠️Mac (cannot talk to local inference server like Ollama due to Mac's Docker limitation)

**Example output:**
```
🚀 Starting Live VLM WebUI Docker Container
Platform detected: x86_64 PC
GPU: NVIDIA RTX 4090 detected
Pulling image: ghcr.io/nvidia-ai-iot/live-vlm-webui:latest
Starting container...
✅ Container started successfully!
Access at: https://localhost:8090
```

---

## 🎯 Why Docker for Jetson?

Docker is **strongly recommended** for Jetson platforms:

✅ **Works immediately** - No platform-specific Python/pip setup
✅ **Isolated environment** - No system package conflicts
✅ **Full GPU monitoring** - jtop included and configured
✅ **Production-ready** - Tested and optimized
✅ **No Python version conflicts** - Self-contained environment
✅ **Easy updates** - `docker pull` to get latest version

**Alternative (pip):** Possible but requires more manual setup. See main README for pip installation.

---

## 📋 Manual Docker Run Commands

For advanced users who want fine-grained control over Docker configuration.

### Jetson Docker runtime check

Before starting a Jetson container, the launcher checks Docker's registered runtimes. On **Jetson Orin (JetPack 6 / L4T R36)** it uses `--runtime=nvidia` only when Docker reports that runtime. If it is missing, the launcher exits before creating a container instead of failing with `unknown or invalid runtime name: nvidia`.

Repair the runtime on the Jetson, then rerun the launcher:

```bash
sudo apt update
sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl daemon-reload && sudo systemctl restart docker
docker info --format '{{json .Runtimes}}'  # must contain "nvidia"
```

### PC (x86_64 with NVIDIA GPU)

```bash
docker run -d \
  --name live-vlm-webui \
  --network host \
  --gpus all \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest

# Access at: https://localhost:8090
```

### PC (x86_64 CPU-only, no GPU)

```bash
docker run -d \
  --name live-vlm-webui \
  -p 8090:8090 \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest

# Access at: https://localhost:8090
```

### Jetson Orin (AGX Orin, Orin Nano, Orin NX)

```bash
docker run -d \
  --name live-vlm-webui \
  --network host \
  --runtime nvidia \
  --privileged \
  -v /run/jtop.sock:/run/jtop.sock:ro \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-jetson-orin

# Access at: https://localhost:8090
```

**Note:** `--privileged` and jtop socket mount are required for GPU monitoring.

### Jetson Thor (AGX Thor)

```bash
docker run -d \
  --name live-vlm-webui \
  --network host \
  --gpus all \
  --privileged \
  -v /run/jtop.sock:/run/jtop.sock:ro \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-jetson-thor

# Access at: https://localhost:8090
```

**Note:** Thor uses `--gpus all` (SBSA-compliant) instead of `--runtime nvidia`.

### Mac (Apple Silicon or Intel)

```bash
docker run -d \
  --name live-vlm-webui \
  -p 8090:8090 \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-mac

# Access at: https://localhost:8090
```

**Note:** No GPU support on Mac in Docker. CPU monitoring only.

### Cloud deployment (config overrides)

When running in the cloud (e.g. Digital Ocean) with the NVIDIA API, you can set default API base and frame interval via environment variables. Pass them with `-e` or `--env-file`:

```bash
docker run -d \
  --name live-vlm-webui \
  -p 8090:8090 \
  -e LIVE_VLM_API_BASE=https://integrate.api.nvidia.com/v1 \
  -e LIVE_VLM_PROCESS_EVERY=150 \
  -e LIVE_VLM_DEFAULT_MODEL=google/gemma-3-4b-it \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest
```

- **`LIVE_VLM_API_BASE`** – Default API base URL when not overridden by CLI (e.g. NVIDIA Integrate).
- **`LIVE_VLM_DEFAULT_MODEL`** – Default model name when not overridden by CLI (e.g. `google/gemma-3-4b-it` for NVIDIA API Catalog).
- **`LIVE_VLM_PROCESS_EVERY`** – Process every Nth frame (e.g. `150` ≈ 5 seconds at 30 fps to reduce API usage).

Using an env file:

```bash
# .env.cloud
LIVE_VLM_API_BASE=https://integrate.api.nvidia.com/v1
LIVE_VLM_PROCESS_EVERY=150
LIVE_VLM_DEFAULT_MODEL=google/gemma-3-4b-it
```

```bash
docker run -d --name live-vlm-webui -p 8090:8090 --env-file .env.cloud ghcr.io/nvidia-ai-iot/live-vlm-webui:latest
```

---

## 🛠️ Container Management

### Stop Container

```bash
./scripts/stop_container.sh
# OR manually:
docker stop live-vlm-webui
```

### Restart Container

```bash
docker restart live-vlm-webui
```

### View Logs

```bash
docker logs live-vlm-webui

# Follow logs in real-time:
docker logs -f live-vlm-webui
```

### Remove Container

```bash
docker rm -f live-vlm-webui
```

### Update to Latest Version

```bash
# Stop and remove old container
docker stop live-vlm-webui
docker rm live-vlm-webui

# Pull latest image
docker pull ghcr.io/nvidia-ai-iot/live-vlm-webui:latest

# Start new container
./scripts/start_container.sh
```

---

## 🏗️ Building Your Own Images

### Build from Source

**For x86_64 PC:**
```bash
docker build -f docker/Dockerfile -t live-vlm-webui:x86 .
```

**For Jetson Orin:**
```bash
docker build -f docker/Dockerfile.jetson-orin -t live-vlm-webui:jetson-orin .
```

**For Jetson Thor:**
```bash
docker build -f docker/Dockerfile.jetson-thor -t live-vlm-webui:jetson-thor .
```

**For Mac:**
```bash
docker build -f docker/Dockerfile.mac -t live-vlm-webui:mac .
```

### Multi-Architecture Build

Build for multiple platforms at once:

```bash
./scripts/build_multiarch.sh
```

This builds images for both amd64 and arm64.

---

## 🌐 Network Modes

### Host Network (Recommended for Local VLM)

Use `--network host` when connecting to services on the same host:

```bash
docker run -d \
  --name live-vlm-webui \
  --network host \
  --gpus all \
  live-vlm-webui:x86
```

**Benefits:**
- ✅ Container can access `localhost:11434` (Ollama)
- ✅ Container can access `localhost:8000` (vLLM, NIM)
- ✅ No port mapping needed

### Bridge Network (For Remote VLM)

Use `-p` port mapping when connecting to remote services:

```bash
docker run -d \
  --name live-vlm-webui \
  -p 8090:8090 \
  --gpus all \
  -e VLM_API_BASE=http://your-vlm-server:8000/v1 \
  -e VLM_MODEL=llama-3.2-11b-vision-instruct \
  live-vlm-webui:x86
```

---

## ⚙️ Environment Variables

Configure the application using environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `VLM_API_BASE` | Auto-detected | VLM API endpoint URL |
| `VLM_MODEL` | Auto-detected | Model name to use |
| `VLM_PROMPT` | "Describe..." | Default prompt |
| `VLM_API_KEY` | - | API key (for cloud services) |
| `PORT` | 8090 | Server port |

---

## 🔒 Custom SSL Certificates

For production deployment with your own SSL certificates:

```bash
docker run -d \
  --name live-vlm-webui \
  -p 8090:8090 \
  -v /path/to/your/cert.pem:/app/cert.pem:ro \
  -v /path/to/your/key.pem:/app/key.pem:ro \
  live-vlm-webui:x86
```

---

## 📦 Available Docker Images

All images are available on GitHub Container Registry:

| Image Tag | Platform | Base Image | Size | GPU Support |
|-----------|----------|------------|------|-------------|
| `latest` | x86_64 PC | Ubuntu 22.04 + CUDA 12.4 | ~1.5GB | NVIDIA GPU |
| `latest-jetson-orin` | Jetson Orin | L4T r36.2 | ~1.2GB | Jetson GPU |
| `latest-jetson-thor` | Jetson Thor | Ubuntu 24.04 + CUDA 13.0 | ~1.3GB | Jetson GPU |
| `latest-mac` | Mac | Ubuntu 22.04 | ~800MB | CPU only |

**Pull specific image:**
```bash
docker pull ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-jetson-orin
```

---

## 📝 Dockerfile Details

### `Dockerfile` - For x86_64 PC/Workstation

**Base Image:** `nvidia/cuda:12.4.1-runtime-ubuntu22.04`
- Includes NVIDIA CUDA runtime libraries for GPU monitoring via NVML
- Enables `pynvml` to query GPU name, utilization, VRAM, temperature, and power
- Compatible with NVIDIA drivers 545+ (GeForce, Quadro, Tesla, etc.)
- Image size: ~1.5GB (compressed)

### `Dockerfile.jetson-orin` - For NVIDIA Jetson Orin

**Base Image:** `nvcr.io/nvidia/l4t-base:r36.2.0` (L4T r36.2.0, JetPack 6.0)
- Optimized for Jetson Orin platform (AGX Orin, Orin Nano, Orin NX)
- Uses `jtop` (jetson-stats from PyPI) for GPU monitoring
- Supports JetPack 6.x
- Image size: ~1.2GB (compressed)

### `Dockerfile.jetson-thor` - For NVIDIA Jetson Thor

**Base Image:** `nvcr.io/nvidia/cuda:13.0.0-runtime-ubuntu24.04`
- **Jetson Thor is SBSA-compliant** - Uses standard NGC CUDA containers (no L4T-specific images needed!)
- This is a major architectural change from previous Jetsons (Orin, Xavier)
- Uses `jtop` (jetson-stats from GitHub) for latest Thor GPU monitoring support
- Ubuntu 24.04 base (aligned with JetPack 7.x)
- Reference: [Jetson Thor CUDA Setup Guide](https://docs.nvidia.com/jetson/agx-thor-devkit/user-guide/latest/setup_cuda.html)

**Why separate Dockerfiles?**
- **Jetson Orin**: Requires L4T-specific base images (`l4t-base:r36.x`)
- **Jetson Thor**: SBSA-compliant, uses standard CUDA containers
- **Monitoring**: Both use `jtop` for GPU stats (NVML limited on Jetson)
- **jetson-stats source**: Orin uses PyPI (stable), Thor uses GitHub (bleeding-edge support)

---

## 🆘 Troubleshooting

### Container won't start

**Check logs:**
```bash
docker logs live-vlm-webui
```

**Common issues:**
- Port 8090 already in use: Use `-p 8091:8090` to map to different port
- GPU not accessible: Ensure NVIDIA Docker runtime is installed
- Permission denied: Try adding `sudo` or check Docker group membership

### GPU monitoring shows "N/A"

**For PC/Workstation:**
- Ensure `--gpus all` flag is used
- Verify NVIDIA Docker runtime: `docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi`

**For Jetson:**
- Ensure jtop is running on host: `sudo systemctl status jtop`
- Verify socket mount: `-v /run/jtop.sock:/run/jtop.sock:ro`
- Check `--privileged` flag is set

### Cannot connect to VLM backend on localhost

**Solution:** Use `--network host` instead of `-p 8090:8090`

**Reason:** Bridge network mode isolates container networking. With `--network host`, container can access `localhost:11434` (Ollama), `localhost:8000` (vLLM), etc.

### Image pull is slow

**Tip:** Pre-pull images on slow connections:
```bash
docker pull ghcr.io/nvidia-ai-iot/live-vlm-webui:latest
```

### Want to use different port

**Map to different port:**
```bash
docker run -d -p 9090:8090 --name live-vlm-webui \
  ghcr.io/nvidia-ai-iot/live-vlm-webui:latest
# Access at: https://localhost:9090
```

---

## 📚 Additional Resources

- **Main Documentation:** [README.md](../../README.md)
- **Troubleshooting Guide:** [docs/troubleshooting.md](../troubleshooting.md)
- **VLM Backend Setup:** See README for Ollama, vLLM, NVIDIA API setup
- **Docker Compose:** See `docker-compose.yml` in repository root

---

## 🤝 Getting Help

**Issues or questions?**
- GitHub Issues: https://github.com/nvidia-ai-iot/live-vlm-webui/issues
- Check troubleshooting guide first
- Include platform info and logs when reporting issues
