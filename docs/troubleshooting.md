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

## Camera Issues

### Camera not accessible

**Issue:** Browser won't allow camera access

**Solutions:**
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

### Multiple cameras not detected

**Issue:** Only one camera shows up in dropdown

**Solutions:**
- Refresh the browser page
- Check `ls /dev/video*` on Linux to see available devices
- Try unplugging and replugging USB cameras
- Restart the server

---

## VLM Backend Issues

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

### Slow VLM inference

**Issue:** VLM takes >10 seconds per frame

**Solutions:**
- Use a smaller/faster model:
  - Try `llava:7b` instead of `llava:34b`
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

