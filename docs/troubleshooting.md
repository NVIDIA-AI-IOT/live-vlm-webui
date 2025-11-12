# Troubleshooting Guide

Common issues and solutions for Live VLM WebUI.

## Installation Issues

### "setup.py" or "setup.cfg" not found error

**Issue:** Running `pip install -e .` fails with:
```
ERROR: File "setup.py" or "setup.cfg" not found.
```

**Solution:** Your pip version is too old to support editable installs with `pyproject.toml` only.

Upgrade pip and build tools first:
```bash
pip install --upgrade pip setuptools wheel
pip install -e .
```

**Common on:**
- macOS with default Python/pip
- Ubuntu/Debian with older Python versions
- Fresh virtual environments with outdated pip

### Package not found after installation

**Issue:** After installing with `pip install -e .`, running the server shows:
```
ModuleNotFoundError: No module named 'live_vlm_webui'
```

**Solutions:**
1. Make sure you're in the correct virtual environment:
   ```bash
   source .venv/bin/activate  # or conda activate your-env
   ```

2. Reinstall the package:
   ```bash
   pip install -e .
   ```

3. Verify installation:
   ```bash
   python -c "import live_vlm_webui; print(live_vlm_webui.__version__)"
   ```

### Wrong Python environment

**Issue:** The `start_server.sh` script says package not found, even though you installed it.

**Solution:** You might be in a different environment than where you installed. The script will show you which environment it detected and give you specific instructions to fix it.

---

### Dependency conflict warning with mlx-vlm (Mac)

**Issue:** On Mac, pip shows a dependency conflict warning during installation:
```
ERROR: pip's dependency resolver does not currently take into account all the packages that are installed. This behaviour is the source of the following dependency conflicts.
mlx-vlm 0.3.2 requires transformers>=4.53.0, but you have transformers 4.51.3 which is incompatible.
```

**Impact:** ✅ **Warning only** - live-vlm-webui installs and runs correctly despite this message.

**Cause:** This is a pre-existing issue with your `mlx-vlm` installation. Our package doesn't use or depend on `transformers`, so the conflict is between `mlx-vlm` and another package in your environment.

**Solution (optional):** If you want to resolve the warning:
```bash
# Upgrade transformers to satisfy mlx-vlm's requirement
pip install --upgrade transformers>=4.53.0
```

**Note:** You can safely ignore this warning - it doesn't affect live-vlm-webui functionality.

---

### pip: command not found (Jetson)

**Issue:** On Jetson, running `pip install` shows:
```
-bash: pip: command not found
```

**Solution:** Use `python3 -m pip` instead, which is more reliable:
```bash
# Use python3 -m pip (works on all systems)
python3 -m pip install live-vlm-webui

# Run the server
python3 -m live_vlm_webui.server
```

**Why this happens:** Jetson doesn't always install the `pip` command by default, but `python3 -m pip` always works because it uses Python's built-in pip module.

**Optional:** If you want to use `pip` directly:
```bash
sudo apt install python3-pip
```

---

### Jetson-Specific Installation

**Issue:** Installation on Jetson Orin/Thor

**Solutions:**

**Option 1: pip install (Recommended for Development)**

**For Jetson Orin (JetPack 6.x / Python 3.10):**
```bash
# Install dependencies
sudo apt install openssl python3-pip

# Install jetson-stats for GPU monitoring (optional but recommended)
sudo pip3 install -U jetson-stats

# Install the package
python3 -m pip install --user live-vlm-webui

# Add to PATH (one-time setup)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Run it
live-vlm-webui
```

**For Jetson Thor (r38.2+ / Python 3.12):**
```bash
# Install dependencies
sudo apt install openssl pipx

# Ensure PATH for pipx
pipx ensurepath
source ~/.bashrc

# Install the package using pipx (required for Python 3.12)
pipx install live-vlm-webui

# Install jetson-stats for GPU monitoring (from GitHub - PyPI version doesn't support Thor yet)
# Step 1: Install system-wide for the jtop service
sudo pip3 install --break-system-packages git+https://github.com/rbonghi/jetson_stats.git
sudo jtop --install-service

# Step 2: Inject into pipx environment so live-vlm-webui can use it
pipx inject live-vlm-webui git+https://github.com/rbonghi/jetson_stats.git

# Step 3: Reboot for jtop service permissions to take effect
sudo reboot

# After reboot, run it
live-vlm-webui
```

**Note:** Thor support for jetson-stats is available on GitHub but not yet released to PyPI. Two installations are needed: system-wide for the service, and in the pipx environment for the app to access it.

**Option 2: Docker (Recommended for Production)**
- See [Jetson Quick Start](../README.md#-jetson-quick-start) in the main README
- Docker avoids all Python environment issues

**Note:** If creating a virtual environment on Jetson fails with "ensurepip is not available":
```bash
# This is only needed for venv creation, not pip install
sudo apt install python3.10-venv
```

However, **you don't need a venv** for basic pip installation! Direct pip install to user site-packages works perfectly on Jetson.

---

### pip: command not found (Jetson)

**Issue:** Running `pip install` shows:
```
-bash: pip: command not found
```

**Solution:** Use `python3 -m pip` instead (more reliable):

```bash
# Instead of: pip install live-vlm-webui
python3 -m pip install live-vlm-webui

# Run with:
python3 -m live_vlm_webui.server
```

**Why this happens:** On Jetson, the `pip` command might not be installed or linked, but `python3 -m pip` always works and guarantees you're using the correct Python's pip.

**Alternative:** If you really want the `pip` command:
```bash
sudo apt install python3-pip
```

---

### Python version error on Jetson (JetPack 5.x)

**Issue:** Installation fails with:
```
ERROR: Package 'live-vlm-webui' requires a different Python: 3.8.10 not in '>=3.10'
```

**Cause:** JetPack 5.x comes with Python 3.8, but live-vlm-webui requires Python 3.10+.

**Solution:** Upgrade to **JetPack 6** which includes Python 3.10:

- **JetPack 5.x** → Python 3.8 ❌ (not supported)
- **JetPack 6.x** → Python 3.10+ ✅ (supported)

**Recommended:**
1. Upgrade your Jetson to JetPack 6 using NVIDIA SDK Manager
2. Or use Docker (recommended for JetPack 5.x users):
   ```bash
   git clone https://github.com/nvidia-ai-iot/live-vlm-webui.git
   cd live-vlm-webui
   ./scripts/start_container.sh
   ```

**Why Python 3.10+?** The project uses modern Python features and dependencies (like `match` statements, typing improvements) that require Python 3.10 or newer.

---

### "externally-managed-environment" error (Jetson Thor)

**Issue:** On Jetson Thor (r38.2+ with Python 3.12), pip install fails with:
```
error: externally-managed-environment
× This environment is externally managed
```

**Cause:** Python 3.12 includes PEP 668 protection to prevent breaking system packages.

**Solution: Use pipx (recommended for Thor):**
```bash
# Install pipx first (one-time)
sudo apt install pipx
pipx ensurepath
source ~/.bashrc

# Install the app (pipx manages everything automatically)
pipx install live-vlm-webui

# Run it
live-vlm-webui
```

**Alternative: Use a virtual environment:**
```bash
# Create a venv
python3 -m venv ~/live-vlm-venv
source ~/live-vlm-venv/bin/activate

# Install normally
pip install live-vlm-webui

# Run it
live-vlm-webui
```

**Why not `--user` or `--break-system-packages`?**
- ⚠️ `--user` still triggers PEP 668 protection on Thor's Python 3.12
- ⚠️ `--break-system-packages` can damage your system's Python environment

**Why pipx is best:** It's designed exactly for this - installing Python CLI applications globally while keeping them isolated. The Python error message even recommends it!

---

### jetson-stats issues on Thor

**Issue 1: Not supported on Thor**
```
[WARN] jetson-stats not supported for [L4T 38.2.0]
```

**Issue 2: Version mismatch**
```
Mismatch version jtop service: [4.3.2] and client: [4.5.2]
```

**Issue 3: Can't access jtop.service**
```
I can't access jtop.service. Please logout or reboot this board.
```

**Complete Solution:**

```bash
# Step 1: Uninstall old version if installed
sudo pip3 uninstall -y jetson-stats

# Step 2: Install latest from GitHub (has Thor support)
sudo pip3 install --break-system-packages git+https://github.com/rbonghi/jetson_stats.git

# Step 3: Install/update the jtop service
sudo jtop --install-service

# Step 4: If using pipx for live-vlm-webui, inject jetson-stats
pipx inject live-vlm-webui git+https://github.com/rbonghi/jetson_stats.git

# Step 5: Reboot for permissions to take effect
sudo reboot

# After reboot, test it
sudo jtop
```

**Alternative to reboot (logout/login):**
```bash
# Add user to jtop group
sudo usermod -a -G jtop $USER

# Then logout and login, or force new group:
newgrp jtop

# Verify socket permissions
ls -l /run/jtop.sock
```

**Why these steps?**
- **GitHub install:** PyPI version doesn't support Thor yet
- **--install-service:** Updates systemd service to match client version
- **pipx inject:** Allows pipx-isolated app to access jetson-stats
- **Reboot/logout:** Sets up socket permissions for jtop service access

---

## Camera Issues

### Server fails to start: "Cannot start server without SSL certificates"

**Issue:** Server exits immediately with:
```
❌ Cannot start server without SSL certificates
❌ Webcam access requires HTTPS!
```

**Solution:** Install openssl to enable automatic SSL certificate generation:

```bash
# Linux/Jetson
sudo apt install openssl

# macOS
brew install openssl

# Then restart the server
live-vlm-webui
```

**Alternative:** If you don't need camera access (testing only), use:
```bash
live-vlm-webui --no-ssl
```

**Why this happens:** Modern browsers require HTTPS for webcam access. The server auto-generates SSL certificates using openssl if it's not installed, the server will fail to start to prevent confusion.

### Camera not accessible

**Issue:** Browser won't allow camera access

**Solutions:**
- ✅ Make sure you're using **HTTPS** (not HTTP)
- ✅ Verify SSL certificates were auto-generated (check server logs for "✅ Generated cert.pem and key.pem")
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

### Multiple cameras not detected

**Issue:** Only one camera shows up in dropdown

**Solutions:**
- Refresh the browser page
- Check `ls /dev/video*` on Linux to see available devices
- Try unplugging and replugging USB cameras
- Restart the server

---

## VLM Backend Issues

> 📖 **Reference:** For a complete list of available Vision-Language Models across different providers, see [List of VLMs](usage/list-of-vlms.md).

### Ollama GPU error on Jetson Thor (r38.2 / JetPack 7.0)

**Issue:** On some Thor systems, Ollama fails to load models with:
```
Error: 500 Internal Server Error: do load request: Post "http://127.0.0.1:XXXXX/load": EOF
```

**Symptom during installation:** Ollama may show:
```
WARNING: Unsupported JetPack version detected. GPU may not be supported
```

**Root cause:** **Ollama 0.12.10 incompatibility with JetPack 7.0 (Thor only)**
- ✅ **Ollama 0.12.9** on Thor (JetPack 7.0) - Works
- ✅ **Ollama 0.12.10** on Orin (JetPack 6.x) - Works
- ❌ **Ollama 0.12.10** on Thor (JetPack 7.0) - GPU inference fails
- **Specific issue:** 0.12.10 introduced code incompatible with Thor's newer CUDA/GPU stack
- Works fine on older JetPack versions (6.x)

**Quick fix:** Downgrade to 0.12.9:
```bash
# Uninstall current version
sudo systemctl stop ollama
sudo systemctl disable ollama
sudo rm -rf /usr/local/bin/ollama /etc/systemd/system/ollama.service

# Install working version 0.12.9
curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION=0.12.9 sh

# Test it
ollama run gemma3:4b "test"
```

**Workarounds:**

1. **Use NVIDIA API Catalog (Recommended for testing):**
   ```bash
   # Get free API key from https://build.nvidia.com
   live-vlm-webui --api-base https://integrate.api.nvidia.com/v1 \
                  --model meta/llama-3.2-11b-vision-instruct \
                  --api-key YOUR_NGC_API_KEY
   ```

2. **Try Ollama in Docker (might have better support):**
   ```bash
   docker run -d --gpus all --runtime nvidia \
     -v ollama:/root/.ollama \
     -p 11434:11434 \
     --name ollama \
     ollama/ollama

   docker exec ollama ollama pull gemma3:4b
   ```

3. **Use alternative VLM backends:**
   - vLLM (better support for new platforms)
   - NVIDIA NIM (if available for Thor)
   - SGLang

4. **Wait for Ollama update:**
   - Track: https://github.com/ollama/ollama/issues
   - Thor support will likely be added in future releases

**Diagnostic steps:**
```bash
# 1. Check Ollama version (most important!)
ollama --version
# If 0.12.10, downgrade to 0.12.9 (see Quick fix above)

# 2. Check system versions
cat /etc/nv_tegra_release
nvidia-smi

# 3. Check Ollama logs for errors
sudo journalctl -u ollama -n 50 | grep -i error

# 4. Test inference
ollama run gemma3:4b "test"
```

**Status:** Confirmed Thor + JetPack 7.0 specific issue with Ollama 0.12.10.

**Testing confirmation (extensive):**
- Jetson Thor (JP 7.0) + 0.12.9 ✅
- Jetson Thor (JP 7.0) + 0.12.10 ❌ **ONLY platform affected**
- Jetson Orin (JP 6.2) + 0.12.10 ✅
- DGX Spark (ARM64) + 0.12.10 ✅
- Mac (x86_64/ARM64) + 0.12.5 ✅

**Upstream tracking:**
- GitHub Issue: https://github.com/ollama/ollama/issues/13033
- Related issue: https://github.com/ollama/ollama/issues/13027
- Issue is specific to JetPack 7.0 (Thor), not general Ollama bug
- Likely CUDA or GPU initialization incompatibility with Thor's newer stack
- **Status:** Confirmed regression in 0.12.10 - use 0.12.9 until resolved

### VLM connection errors

**Issue:** Cannot connect to VLM API

**Solutions:**
- Verify your VLM backend is running
- Check the API base URL matches your backend's port:
  - vLLM: `http://localhost:8000/v1`
  - SGLang: `http://localhost:30000/v1`
  - Ollama: `http://localhost:11434/v1`
- Test with curl:
  ```bash
  curl http://localhost:8000/v1/models
  ```
- Check firewall settings
- Ensure `--network host` if using Docker with local VLM

### "Model not found" errors

**Issue:** VLM API returns model not found

**Solutions:**
- Ensure the model is loaded in your backend
- Model names must match exactly (case-sensitive)
- For Ollama: `ollama list` to see available models
- For vLLM: Check startup logs for loaded model name
- Click "🔄 Refresh" in the UI to re-detect models
- See [List of VLMs](usage/list-of-vlms.md) for correct model names by provider

### VLM output is non-relevant or generic (hallucinating)

**Issue:** The VLM generates plausible-sounding descriptions that don't match what's actually in the video/image.

**Example:**
- Camera shows a person at a desk
- VLM says: "The image appears to be a serene landscape with rolling hills, a clear blue sky above and possibly wildflowers dotting the terrain at its base."

**Root Cause:** ⚠️ **You selected a TEXT-ONLY model instead of a VISION model!**

The text-only model doesn't actually see the image - it's just generating plausible text based on the prompt. This is called "hallucination."

**Solution:** Use a **vision-capable** model:

> 📖 **See also:** [Complete List of Vision-Language Models](usage/list-of-vlms.md) - Comprehensive guide to all available VLMs across Ollama, NVIDIA, OpenAI, and Anthropic.

**✅ Correct Models (Vision):**
- `llama3.2-vision:11b` (Ollama)
- `llama3.2-vision:90b` (Ollama)
- `llava:7b`, `llava:13b` (Ollama) - ⚠️ Note: `llava:34b` is text-only
- `moondream:latest` (Ollama)
- `phi3.5-vision` (vLLM/HuggingFace)
- `microsoft/phi-3-vision-128k-instruct` (NVIDIA API Catalog)
- `meta/llama-3.2-90b-vision-instruct` (NVIDIA API Catalog)
- `gpt-5`, `gpt-4o`, `gpt-4-vision-preview` (OpenAI)

**❌ Incorrect Models (Text-Only - Will Hallucinate):**
- `llama3.1:8b` ❌ (no vision)
- `phi3.5:3.8b` ❌ (no vision - this is text-only!)
- `phi3:14b` ❌ (no vision)
- `gemma2:9b` ❌ (no vision)
- `mistral:7b` ❌ (no vision)

**How to verify your model supports vision:**
```bash
# For Ollama - check model details
ollama show llama3.2-vision:11b

# Look for "vision" in the model name or architecture
# Vision models typically have "vision", "llava", or "multimodal" in the name
```

**Quick test:**
1. Point camera at something distinctive (a colored object, text, etc.)
2. Ask: "What color is the object in front of the camera?"
3. If the response is generic or unrelated → you're using a text-only model

**Why does this happen?**
Text-only models can't process images, so they:
1. Ignore the image data
2. Generate text based solely on your prompt
3. Create plausible-sounding but incorrect descriptions

**Fix:**
```bash
# Pull a vision model
ollama pull llama3.2-vision:11b

# In Live VLM WebUI settings:
# Model: llama3.2-vision:11b  (not llama3.1:8b or phi3.5:3.8b)
```

### Slow VLM inference

**Issue:** VLM takes >10 seconds per frame

**Solutions:**
- Use a smaller/faster model:
  - Try `llava:7b` instead of `llava:13b`
  - Try `phi-3-vision` (4B parameters)
- Increase `Frame Processing Interval` to process fewer frames
- Reduce `Max Tokens` in settings (e.g., 50-100 instead of 512)
- Ensure your VLM backend is using GPU acceleration:
  ```bash
  nvidia-smi  # Check GPU utilization while processing
  ```
- For vLLM: Add `--dtype float16` or `--quantization awq` for speed

---

## Docker Issues

### "NVML not available" in Docker

**Issue:** GPU monitoring shows "N/A" or NVML errors

**Solutions:**

**1. Check if nvidia-container-toolkit is installed:**
```bash
which nvidia-container-runtime
nvidia-container-cli --version
```

**2. Install if missing:**
```bash
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

**3. Verify GPU access:**
```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

**4. If `--gpus all` doesn't work, try CDI:**
```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
docker run --rm --device nvidia.com/gpu=all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

### Container can't access localhost services

**Issue:** WebUI container can't find Ollama/vLLM on localhost

**Solution:** Use `--network host`:
```bash
docker run -d \
  --name live-vlm-webui \
  --network host \  # <-- Important!
  --gpus all \
  live-vlm-webui:x86
```

With `--network host`, the container shares the host's network stack, so `localhost` refers to the host.

### Docker Compose fails with "unknown shorthand flag: 'f'"

**Issue:** Using `docker compose` instead of `docker-compose`

**Solution:** Install docker-compose:
```bash
sudo apt install -y docker-compose

# Then use with hyphen:
docker-compose --profile live-vlm-webui-x86 up
```

Or upgrade Docker to support `docker compose` (newer syntax):
```bash
# Install Docker Compose V2
sudo apt update
sudo apt install -y docker-compose-plugin
```

---

## Performance Issues

### Video stream is laggy

**Issue:** Video has high latency or stutters

**Solutions:**
- Reduce video resolution in browser settings
- Close other applications using the camera
- Increase "Max Video Latency" threshold in settings
- Check network connection if accessing remotely
- Try a different browser (Chrome/Edge recommended)

### High CPU usage

**Issue:** CPU at 100% constantly

**Solutions:**
- Increase "Frame Processing Interval" (process fewer frames)
  - Default is 30 frames (~1 analysis per second @ 30fps)
  - Try 60-90 frames for lower CPU usage
- Reduce video resolution
- Use hardware acceleration (future feature for Jetson)

### Frame dropping warnings

**Issue:** Logs show "Frame is X.XXs behind, dropping frames"

**This is normal behavior!** The system is preventing latency accumulation.

**To adjust tolerance:**
- Increase "Max Video Latency" in WebRTC settings
  - 0 = disabled (no frame dropping)
  - 1.0 = drop if >1 second behind (default)
  - 2.0+ = more tolerant

---

## System Monitoring Issues

### GPU stats show "N/A"

**Issue:** GPU utilization, VRAM, etc. show "N/A"

**Solutions:**

**For PC (x86_64):**
- Ensure `--gpus all` or `--device nvidia.com/gpu=all` is used
- Check NVML installation: `python3 -c "import pynvml; pynvml.nvmlInit()"`
- Install pynvml: `pip install nvidia-ml-py3`

**For Jetson:**
- Ensure `--privileged` flag is used
- Mount jtop socket: `-v /run/jtop.sock:/run/jtop.sock:ro`
- Check jtop on host: `sudo jtop`
- Install jetson-stats: `pip install jetson-stats`

### System stats not updating

**Issue:** GPU/CPU stats frozen

**Solutions:**
- Check WebSocket connection (green indicator in header)
- Refresh the browser page
- Check server logs: `docker logs live-vlm-webui`
- Restart the container

---

## Network Issues

### Can't access from another device

**Issue:** WebUI only accessible from localhost

**Solutions:**
- Check `--host` flag: should be `0.0.0.0` not `127.0.0.1`
- Verify firewall allows port 8090:
  ```bash
  sudo ufw allow 8090/tcp
  ```
- Use HTTPS (not HTTP) - browsers require it for camera access
- Find your IP: `hostname -I`
- Access from other device: `https://<your-ip>:8090`

### WebSocket disconnects frequently

**Issue:** "Disconnected" message appears often

**Solutions:**
- Check network stability
- Reduce WebSocket message frequency (modify `gpu_monitoring_task` in server.py)
- Try wired connection instead of Wi-Fi
- Check server logs for errors

---

## Build Issues

### "No space left on device" during Docker build

**Solution:**
```bash
# Clean up Docker
docker system prune -af
docker volume prune -f

# Check disk space
df -h
```

### Python dependency conflicts

**Issue:** `pip install -r requirements.txt` fails

**Solutions:**
- Use a virtual environment:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  ```
- Update pip:
  ```bash
  pip install --upgrade pip
  ```
- Install dependencies one by one to find the culprit

### ARM64 build fails on x86_64

**Issue:** Building Jetson images on PC fails

**Solution:** Install QEMU for emulation:
```bash
sudo apt-get install qemu-user-static
docker buildx create --use
docker buildx build --platform linux/arm64 -f Dockerfile.jetson-orin .
```

Or build on native Jetson hardware.

---

## Getting Help

If you're still stuck:

1. **Check the logs:**
   ```bash
   # Docker container
   docker logs live-vlm-webui

   # Manual installation
   ./start_server.sh  # Logs appear in terminal
   ```

2. **Search existing issues:**
   - https://github.com/nvidia-ai-iot/live-vlm-webui/issues

3. **Open a new issue:**
   - Include: Platform (PC/Jetson), Docker or manual, error messages, logs
   - Template: https://github.com/nvidia-ai-iot/live-vlm-webui/issues/new

4. **Community support:**
   - NVIDIA Developer Forums: https://forums.developer.nvidia.com/

---

## Debug Mode

Enable verbose logging for troubleshooting:

```bash
# Set log level to DEBUG
python server.py --log-level DEBUG

# Or via environment variable
export LOG_LEVEL=DEBUG
./start_server.sh
```

This will show detailed information about:
- WebRTC negotiation
- VLM API calls
- Frame processing
- GPU monitoring
- WebSocket messages
