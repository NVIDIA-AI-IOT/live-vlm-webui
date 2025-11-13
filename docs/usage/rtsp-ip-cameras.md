# RTSP IP Camera Support

**Added in:** v0.2.0
**Status:** Beta

Live VLM WebUI now supports RTSP streams from IP cameras, enabling continuous monitoring and analysis of security cameras, baby monitors, and other network video sources.

---

## 🎥 Overview

With RTSP support, you can:
- Process video from any RTSP-compatible IP camera
- Monitor multiple camera feeds (coming soon)
- Run continuous AI analysis on surveillance cameras
- Use Jetson as a dedicated edge AI appliance
- Analyze video without needing a webcam connected to your browser

---

## 🚀 Quick Start

### 1. Select RTSP Input Source

1. Open Live VLM WebUI at `https://localhost:8090`
2. In the **Camera and App Control** panel, change **Input Source** from "Webcam" to "RTSP Stream"
3. Enter your RTSP URL in the format: `rtsp://[user:pass@]ip:port/path`
4. Click **Test Connection** to verify connectivity (optional but recommended)
5. Click **Connect to Stream and Start VLM Analysis**

### 2. Example RTSP URLs

```bash
# Generic format
rtsp://192.168.1.100:554/stream

# With authentication
rtsp://admin:password@192.168.1.100:554/stream1

# Reolink cameras
rtsp://admin:password@192.168.1.100:554/h264Preview_01_main
rtsp://admin:password@192.168.1.100:554/h264Preview_01_sub  # Lower resolution

# Hikvision
rtsp://admin:password@192.168.1.101:554/Streaming/Channels/101
rtsp://admin:password@192.168.1.101:554/Streaming/Channels/102  # Sub-stream

# Dahua
rtsp://admin:password@192.168.1.102:554/cam/realmonitor?channel=1&subtype=0
rtsp://admin:password@192.168.1.102:554/cam/realmonitor?channel=1&subtype=1  # Sub-stream

# ONVIF standard
rtsp://192.168.1.103:554/onvif1
rtsp://admin:password@192.168.1.103:554/onvif1

# Wyze cameras (with RTSP firmware)
rtsp://192.168.1.104:554/live

# TP-Link/Tapo
rtsp://admin:password@192.168.1.105:554/stream1
rtsp://admin:password@192.168.1.105:554/stream2  # Lower quality
```

---

## 📋 Finding Your Camera's RTSP URL

### Method 1: Check Camera Documentation

Most IP cameras document their RTSP URL format in the manual or manufacturer's website.

**Common manufacturers:**
- **Reolink:** Usually port 554, path `/h264Preview_01_main`
- **Hikvision:** Usually `/Streaming/Channels/101`
- **Dahua:** Usually `/cam/realmonitor?channel=1&subtype=0`
- **Amcrest:** Similar to Dahua
- **Wyze:** Requires RTSP firmware, then `/live`
- **Generic ONVIF:** Try `/onvif1`, `/onvif2`, or `/stream1`

### Method 2: Use ONVIF Device Manager

1. Download [ONVIF Device Manager](https://sourceforge.net/projects/onvifdm/)
2. Scan your network for cameras
3. View camera's RTSP URL in the "Live Video" section

### Method 3: Check Camera Web Interface

1. Log into your camera's web interface (usually `http://camera-ip`)
2. Look for "Streaming", "RTSP", or "Network" settings
3. RTSP URL is often displayed there

### Method 4: Trial and Error

Try common patterns:
```bash
# Try default port and common paths
rtsp://camera-ip:554/stream
rtsp://camera-ip:554/stream1
rtsp://camera-ip:554/live
rtsp://camera-ip:554/h264
rtsp://camera-ip:8554/stream
```

---

## 🎬 Use Cases

### Home Safety Monitoring

**Pool Safety (Child Drowning Detection):**
```
Prompt: "Is there a person underwater who is not moving?
        Is anyone showing signs of distress in the water?
        Are there any children alone near the pool?"
```

**Elder Care (Fall Detection):**
```
Prompt: "Is there a person lying on the floor?
        Has anyone fallen down?
        Is the person moving normally?"
```

### Home Surveillance

**Security Monitoring:**
```
Prompt: "Describe any people or vehicles visible.
        Is there any unusual or suspicious activity?
        Are there any packages at the door?"
```

**Pet Monitoring:**
```
Prompt: "What is the dog doing?
        Is the cat trying to escape?
        Are the pets safe and behaving normally?"
```

### Business Applications

**Retail:**
```
Prompt: "How many customers are visible?
        Is the checkout line long?
        Are there any safety concerns?"
```

**Warehouse Safety:**
```
Prompt: "Are workers wearing safety equipment?
        Is anyone in a restricted area?
        Are there any safety violations visible?"
```

---

## ⚙️ Configuration

### Frame Processing Interval

For continuous monitoring, adjust the **Frame Processing Interval**:

- **High frequency (5-30 frames):** More responsive, higher GPU usage
- **Standard (30-60 frames):** Balanced performance
- **Low frequency (120-300 frames):** Power-efficient, suitable for slow-changing scenes

Example: At 30fps camera, processing every 30 frames = 1 analysis per second.

### Recommended Settings by Use Case

**Real-time Safety Monitoring (Pool, Elder Care):**
- Frame Interval: **30** (1 sec at 30fps)
- Max Tokens: **100-150** (quick responses)
- Model: Fast model (e.g., `llama3.2-vision:11b`)

**Security Surveillance:**
- Frame Interval: **60-120** (2-4 seconds)
- Max Tokens: **150-256** (detailed descriptions)
- Model: Balanced model

**Periodic Monitoring (Pet cam, slow scenes):**
- Frame Interval: **300-600** (10-20 seconds)
- Max Tokens: **100**
- Model: Any model

---

## 🔧 Technical Details

### Supported Protocols

- **RTSP over TCP** (default, more reliable)
- **RTSP over UDP** (lower latency, less reliable)
- **H.264 video codec** (most common)
- **H.265 / HEVC** (if FFmpeg supports it)
- **MJPEG** (less efficient but widely compatible)

### Network Requirements

- **Local network:** Recommended for reliability and privacy
- **Port forwarding:** Not recommended for security reasons
- **VPN:** Acceptable for remote access
- **Bandwidth:** Minimal - only metadata sent to VLM (not video stream)

### Latency

Typical latency breakdown:
- **RTSP connection:** 500ms - 2s (initial connection)
- **Frame decode:** 10-50ms (depends on codec and resolution)
- **VLM inference:** 1-5s (depends on model and hardware)
- **Total:** ~2-7 seconds from camera to VLM response

For time-critical applications, use lower frame intervals and faster models.

---

## 🔒 Security & Privacy

### ⚠️ Important Security Notes

1. **Never expose RTSP publicly without encryption**
   - RTSP is not encrypted by default
   - Credentials are sent in plaintext
   - Use local network only or VPN for remote access

2. **Change default camera passwords**
   - Default passwords are easily exploited
   - Use strong, unique passwords

3. **URL credential visibility**
   - RTSP URLs with passwords are visible in browser network tab
   - Credentials are sanitized in server logs (password replaced with ****)
   - Don't share screenshots containing RTSP URLs

4. **Data processing**
   - Video is processed locally (not sent to cloud)
   - VLM inference happens on your hardware
   - Only analyzed text results are displayed

5. **Legal compliance**
   - Ensure you comply with local surveillance laws
   - Inform people they are being monitored if required
   - Don't use for unauthorized surveillance

---

## 🐛 Troubleshooting

### Connection Failed

**Error: "Failed to connect to RTSP stream"**

**Solutions:**
1. **Verify RTSP URL:**
   - Check IP address, port, and path
   - Try accessing via VLC: `Media > Open Network Stream > Enter RTSP URL`

2. **Check network connectivity:**
   ```bash
   ping camera-ip
   telnet camera-ip 554  # RTSP port
   ```

3. **Verify credentials:**
   - Ensure username and password are correct
   - Special characters in password may need URL encoding
     - Example: `@` becomes `%40`, `#` becomes `%23`

4. **Firewall/NAT:**
   - Ensure port 554 (RTSP) is not blocked
   - If running in Docker, use `--network host`

5. **Camera limits:**
   - Some cameras limit concurrent RTSP connections (typically 2-5)
   - Close other viewers (VLC, Blue Iris, etc.)

### Stream Drops / Disconnects

**Error: "RTSP stream ended unexpectedly"**

**Solutions:**
1. **Network stability:**
   - Use wired Ethernet instead of WiFi for camera
   - Check for network congestion

2. **Camera settings:**
   - Lower resolution/bitrate in camera settings
   - Enable "Consistent Bitrate" (CBR) instead of "Variable Bitrate" (VBR)

3. **Auto-reconnection:**
   - RTSP track automatically attempts reconnection
   - Check server logs for reconnection attempts

### No Video Display in UI

**Note: This is expected behavior in current version.**

- RTSP mode processes video on backend only
- Video element in UI won't show the stream
- VLM analysis results are still displayed
- **Future enhancement:** Server-sent frame display

### High CPU Usage

**Backend is decoding video frames continuously**

**Solutions:**
1. **Use lower resolution stream:**
   - Use camera's "sub-stream" instead of "main stream"
   - Example: Hikvision `/Streaming/Channels/102` vs `/101`

2. **Increase frame interval:**
   - Process every 120-300 frames for less frequent analysis
   - Still monitors continuously but reduces load

3. **Hardware acceleration (coming soon):**
   - NVDEC on Jetson for GPU-accelerated decoding

---

## 🧪 Testing Without IP Camera

If you don't have an IP camera, you can test RTSP support using:

### Option 1: FFmpeg RTSP Server

Serve a video file as RTSP stream:

```bash
# Install FFmpeg
sudo apt install ffmpeg

# Serve video file as RTSP
ffmpeg -re -stream_loop -1 -i video.mp4 -c copy -f rtsp rtsp://127.0.0.1:8554/test
```

Then connect to: `rtsp://127.0.0.1:8554/test`

### Option 2: MediaMTX (RTSP Server)

```bash
# Download MediaMTX
wget https://github.com/bluenviron/mediamtx/releases/latest/download/mediamtx_vX.X.X_linux_amd64.tar.gz
tar -xzf mediamtx_*.tar.gz
./mediamtx &

# Publish from FFmpeg
ffmpeg -re -stream_loop -1 -i video.mp4 -c copy -f rtsp rtsp://localhost:8554/test

# Connect from live-vlm-webui
# rtsp://localhost:8554/test
```

### Option 3: Virtual Camera Simulators

- **IP Camera Simulator:** http://www.ispyconnect.com/
- **ONVIF Camera Emulator**
- **VLC as RTSP server**

---

## 📊 Performance Benchmarks

Tested on various platforms:

### NVIDIA Jetson AGX Orin

| Camera Resolution | Frame Interval | CPU Usage | GPU Usage | VLM Latency |
|-------------------|----------------|-----------|-----------|-------------|
| 1920x1080 (H.264) | 30 frames      | ~15%      | ~40%      | 2.5s        |
| 1920x1080 (H.264) | 120 frames     | ~8%       | ~40%      | 2.5s        |
| 1280x720 (H.264)  | 30 frames      | ~10%      | ~40%      | 2.0s        |

*VLM: llama3.2-vision:11b on Ollama*

### Desktop PC (RTX 4090)

| Camera Resolution | Frame Interval | CPU Usage | GPU Usage | VLM Latency |
|-------------------|----------------|-----------|-----------|-------------|
| 1920x1080 (H.264) | 30 frames      | ~5%       | ~20%      | 0.8s        |
| 3840x2160 (H.265) | 30 frames      | ~12%      | ~20%      | 1.2s        |

*VLM: llama3.2-vision:90b on vLLM*

---

## 🚧 Known Limitations

1. **Single stream per session** (current version)
   - Workaround: Run multiple instances on different ports
   - Future: Multi-stream support in single UI

2. **No video display in UI** (current version)
   - Backend processes frames but doesn't send to browser
   - Future: Server-sent frame preview

3. **RTSP only, no other protocols** (current version)
   - Future: HTTP/MJPEG, HLS, ONVIF Profile S

4. **CPU-based decode** (current version)
   - Future: Hardware-accelerated decode with NVDEC (Jetson)

---

## 🛠️ Advanced Configuration

### Custom FFmpeg Options

For advanced users, you can modify `rtsp_track.py`:

```python
# Custom RTSP options
options = {
    'rtsp_transport': 'tcp',      # or 'udp' for lower latency
    'max_delay': '500000',        # 500ms
    'rtsp_flags': 'prefer_tcp',
    'timeout': '5000000',         # 5 second timeout
    'buffer_size': '2097152',     # 2MB buffer
}

track = RTSPVideoTrack(rtsp_url, options=options)
```

### Multiple Streams (Manual Setup)

Run multiple instances:

```bash
# Terminal 1: Instance for camera 1
live-vlm-webui --port 8090

# Terminal 2: Instance for camera 2
live-vlm-webui --port 8091

# Terminal 3: Instance for camera 3
live-vlm-webui --port 8092
```

Access at:
- `https://localhost:8090` - Camera 1
- `https://localhost:8091` - Camera 2
- `https://localhost:8092` - Camera 3

---

## 📖 Related Documentation

- [VLM Backend Setup](../setup/vlm-backends.md) - Configure VLM inference
- [Advanced Configuration](advanced-configuration.md) - Performance tuning
- [Troubleshooting Guide](../troubleshooting.md) - Common issues

---

## 💡 Tips & Best Practices

1. **Start with sub-streams:** Use lower resolution streams initially for testing
2. **Test connection first:** Always use "Test Connection" before starting analysis
3. **Monitor CPU usage:** Use system stats panel to check performance
4. **Adjust frame interval:** Find balance between responsiveness and efficiency
5. **Use local network:** Keep cameras and server on same LAN for best performance
6. **Prompt engineering:** Tailor prompts to your specific monitoring needs
7. **Model selection:** Faster models (11B) for real-time, larger models (90B) for accuracy

---

## 🤝 Feedback & Support

Found a bug or have a feature request? Please [open an issue](https://github.com/nvidia-ai-iot/live-vlm-webui/issues).

**Example issues we'd love to hear about:**
- Camera compatibility (which brands/models work/don't work)
- Performance on different hardware
- Use case success stories
- Feature requests (multi-stream, recording, etc.)

---

## 📝 Version History

**v0.2.0 (TBD):**
- Initial RTSP support
- Single stream processing
- Auto-reconnection
- Test connection feature

**Future enhancements:**
- Multi-stream support
- Video preview in UI
- Hardware-accelerated decode
- Recording/snapshot on events
- ONVIF protocol support
- MJPEG/HLS stream support
