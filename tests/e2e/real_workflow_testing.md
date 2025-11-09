# Real Workflow E2E Testing

This guide explains how to run **real end-to-end workflow tests** that use actual video input and VLM inference.

## ⚠️ Automated vs Manual Testing

### Automated Testing (CI/Headless) ✅ **WORKS WITH REAL VIDEO!**

**Solution: Chrome Fake Device with .y4m Video File**

These tests use Chrome's `--use-file-for-fake-video-capture` to provide **real video content** in automated tests:

```bash
# Default: Automated test with fake video device (Y4M file)
pytest tests/e2e/test_real_workflow.py -v -s

# Optional: Test with REAL camera (/dev/video0 or OBS Virtual Camera)
USE_REAL_CAMERA=1 pytest tests/e2e/test_real_workflow.py -v -s
```

**Test video storage:**
- Source files: `tests/e2e/.test-data/` (persistent, ~1.1 GB)
- Recordings: `test-results/videos/` (cleaned by Playwright)

**What WORKS in automated mode:**
- ✅ **Real video content** from Big Buck Bunny test file
- ✅ **Actual VLM inference** with meaningful scene descriptions
- ✅ **Full workflow validation** (video → processing → VLM → display)
- ✅ Server responds correctly
- ✅ Page loads without errors
- ✅ UI buttons render and are clickable
- ✅ WebSocket connection establishes
- ✅ No JavaScript crashes or console errors
- ✅ Video recordings show actual content

**Requirements:**
1. Test video converted to `.y4m` format (uncompressed, ~1GB for 30s)
2. See "Setup" section below for conversion commands

**Use for:** CI pipelines, automated regression testing, smoke tests

### Manual Testing (Real Desktop) 🖥️

**For FULL video validation, test manually:**

1. **On a real desktop with display** (not SSH/headless)
2. **Open browser manually** and navigate to `https://localhost:8090`
3. **Click "Open Camera"** and grant permissions when prompted
4. **Observe:**
   - Live video feed appears
   - VLM analysis overlays appear on video
   - GPU stats update in real-time
   - Analysis changes as video content changes

**Use for:** Feature development, visual QA, debugging video issues

### Manual Testing Checklist ✅

When testing manually on a real desktop:

**Setup:**
- [ ] Server running on `localhost:8090`
- [ ] Ollama running with VLM model loaded (`ollama pull llama3.2-vision:11b`)
- [ ] Real webcam connected OR FFmpeg virtual camera with test video
- [ ] Open browser to `https://localhost:8090`

**Test Flow:**
- [ ] Page loads without errors (check browser console)
- [ ] "Open Camera" button is visible
- [ ] Click "Open Camera" → browser asks for camera permission
- [ ] Grant permission → video feed appears in viewport
- [ ] Camera dropdown shows detected camera device
- [ ] WebSocket connects (see "CONNECTED" indicator)
- [ ] GPU stats display and update every ~1 second
- [ ] VLM analysis text overlay appears on video (wait 2-3 seconds)
- [ ] Analysis updates as video content changes
- [ ] Change model → analysis continues with new model
- [ ] Adjust frame interval → see "Processing every N frames"
- [ ] Stop button works → video stops, analysis stops

**What to Look For:**
- ✅ No console errors
- ✅ Video is smooth, not stuttering
- ✅ VLM analysis is relevant to video content
- ✅ GPU utilization spikes during inference
- ✅ Analysis latency is reasonable (<5s for first result)

## Quick Setup

**Create test video file (.y4m format):**

```bash
# 1. Download Big Buck Bunny test video (Creative Commons)
wget http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4 -O test-video.mp4

# 2. Convert to .y4m format (first 30 seconds, ~1GB uncompressed)
ffmpeg -i test-video.mp4 -t 30 -pix_fmt yuv420p test-video.y4m
```

**Run tests:**

```bash
# Start server and Ollama first
python -m live_vlm_webui.server  # Terminal 1
ollama serve                      # Terminal 2

# Run tests
pytest tests/e2e/test_real_workflow.py -v
```

## What These Tests Do

Unlike the quick smoke tests, these tests:
- ✅ Use **real video content** via Chrome fake device
- ✅ Run **actual VLM inference** (llama3.2-vision:11b)
- ✅ Test the **complete pipeline** (WebRTC → Processing → VLM → Display)
- ✅ **Capture VLM analysis texts** describing video scenes
- ✅ Create **video recordings** showing real usage
- ✅ Monitor **WebSocket connections** and UI interactions
- ✅ Verify **analysis updates** over time

## Requirements

### 1. **Server Running**
```bash
python -m live_vlm_webui.server
```

### 2. **Ollama with VLM Model**
```bash
# Start Ollama
ollama serve

# Pull a fast VLM model for testing
ollama pull gemma3:4b
ollama pull llama3.2-vision:11b
```

### 3. **Video Input Source**

Choose ONE of these options:

#### Option A: Test Video + FFmpeg Virtual Camera (Best for Automated Testing) ⭐

Use a Creative Commons test video with FFmpeg loopback device:

```bash
# 1. Download test video (Big Buck Bunny, Creative Commons)

# Recommended: 720p HD from Google (~158MB, 9:56 duration, has captions) ⭐
wget http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4 -O test-video.mp4

# Or with curl:
curl -o test-video.mp4 http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4

# 2. Create virtual video device (Linux only)
sudo modprobe v4l2loopback

# 3. Feed the video into the virtual device (loops continuously)
ffmpeg -re -stream_loop -1 -i test-video.mp4 -f v4l2 /dev/video0
```

**Benefits:**
- ✅ **HD 720p resolution** - Clear enough for VLM text recognition
- ✅ **Has captions** at 00:21-00:25 for VLM to read
- ✅ Reproducible (same video every time)
- ✅ Creative Commons licensed (Blender Foundation)
- ✅ Reliable source (Google Cloud Storage)
- ✅ Works for CI/automated testing
- ✅ Video loops continuously

**Alternative test video options:**
```bash
# Shorter clip (~10 seconds, 720p) - For quick tests
wget https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_1MB.mp4 -O test-video.mp4

# 1080p HD from Blender (~263MB ZIP, need to extract)
wget https://download.blender.org/demo/movies/BBB/bbb_sunflower_1080p_30fps_normal.mp4.zip
unzip bbb_sunflower_1080p_30fps_normal.mp4.zip
mv bbb_sunflower_1080p_30fps_normal.mp4 test-video.mp4
rm bbb_sunflower_1080p_30fps_normal.mp4.zip

# 4K Ultra HD from Blender (~900MB ZIP, need to extract)
wget https://download.blender.org/demo/movies/BBB/bbb_sunflower_2160p_30fps_normal.mp4.zip
unzip bbb_sunflower_2160p_30fps_normal.mp4.zip
mv bbb_sunflower_2160p_30fps_normal.mp4 test-video.mp4
rm bbb_sunflower_2160p_30fps_normal.mp4.zip

# Sample from test-videos.co.uk (various sizes available)
# 360p Small (~2.5MB): https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4
# 720p Medium (~5MB): https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_5MB.mp4
```

#### Option B: OBS Virtual Camera (Best for Realistic/Manual Testing)
1. Install OBS Studio
2. Set up a scene (screen capture, video file, etc.)
3. Start Virtual Camera in OBS
4. The system will have `/dev/video0` (Linux) or virtual camera device

#### Option C: Chrome with Fake Device (Simplest for Quick Tests)
Playwright can use Chrome's built-in fake video:
```bash
# No setup needed - Chrome provides synthetic video
# Tests will automatically use Chrome's fake media stream
```

## Running The Tests

### Full Workflow Test (~30 seconds)

```bash
# Make sure server is running first!
pytest tests/e2e/test_real_workflow.py::test_full_video_analysis_workflow -v -s
```

This test will:
1. Load the page
2. Start video stream
3. Wait for VLM analysis (up to 20s)
4. Monitor updates for 10s
5. Take a screenshot
6. Create a **~30 second video** in `test-results/videos/`

### Model Switching Test

```bash
pytest tests/e2e/test_real_workflow.py::test_model_switching_during_inference -v -s
```

Tests that you can change VLM models during active inference.

### Performance Under Load Test

```bash
pytest tests/e2e/test_real_workflow.py::test_performance_under_load -v -s
```

Configures for maximum speed (process every frame) and monitors performance for 15 seconds.

### Real Camera Device Test

```bash
# Only works if /dev/video0 exists
pytest tests/e2e/test_real_workflow.py::test_with_real_camera_device -v -s
```

### Run All Real Workflow Tests

```bash
pytest tests/e2e/test_real_workflow.py -v -s
```

**Note:** These are marked `@pytest.mark.slow` and will be skipped unless you add `--runslow` or they're in the test name.

## Viewing The Results

### Videos

After running tests, check:
```bash
ls -lh test-results/videos/
```

Videos are in `.webm` format. Open with:
- **Browser**: Drag into Chrome/Firefox
- **VLC**: `vlc test-results/videos/test_full_video_analysis_workflow-chromium.webm`
- **mpv**: `mpv test-results/videos/*.webm`

### Screenshots

```bash
ls -lh test-results/*.png
```

### Console Output

The tests print detailed progress:
```
🎬 Starting full video analysis workflow test...
   ✅ Page loaded
   📹 Waiting for video stream to start...
   ✅ Video element visible
   ✅ WebSocket connected (GPU stats visible)
   🤖 Waiting for VLM analysis (up to 20 seconds)...
   ✅ VLM analysis detected!
   📊 Monitoring analysis updates for 10 seconds...
   GPU: 89.3%
   🔄 Analysis updated at 3s
   GPU: 100.0%
   🔄 Analysis updated at 7s
   ...
```

## Troubleshooting

### "Server not running on localhost:8090"

Start the server first:
```bash
python -m live_vlm_webui.server
```

### "Ollama not running on localhost:11434"

Start Ollama:
```bash
ollama serve
```

### "No VLM analysis detected after 20s"

This can happen if:
- Model is not loaded (first inference is slow)
- GPU is slow on the test hardware
- Camera is not providing frames
- Model selection is wrong

**Solutions:**
1. Pre-warm the model:
   ```bash
   curl http://localhost:11434/api/generate -d '{
     "model": "gemma3:4b",
     "prompt": "test",
     "stream": false
   }'
   ```

2. Check camera is working:
   - Open Chrome to `chrome://webrtc-internals/`
   - Verify video track is active

3. Check server logs for errors

### Video is too fast/short

The tests are designed to be fast (~30 seconds). If you want longer recordings:

1. Edit `test_real_workflow.py`
2. Increase sleep times:
   ```python
   time.sleep(10)  # Change to time.sleep(60) for 1 minute
   ```

### Tests are skipped

These tests are automatically **skipped in CI** (no GPU/camera). They only run locally.

If skipped locally, check:
- Is server running?
- Is Ollama running?
- Remove `@pytest.mark.skipif` if you want to force it

## CI vs Local

| Environment | Quick Smoke Tests | Real Workflow Tests |
|-------------|-------------------|---------------------|
| **Local Dev** | ✅ Run | ✅ Run |
| **GitHub CI** | ✅ Run | ❌ Skip (no GPU) |

This ensures CI is fast while developers can test the full workflow locally.

## Tips for Better Test Videos

### 1. Use Interesting Video Content

Instead of a static image, use:
- Screen recording showing activity
- Video file with motion
- Actual webcam feed

### 2. Run in Headed Mode

See the browser window during testing:
```bash
pytest tests/e2e/test_real_workflow.py -v -s --headed
```

### 3. Slow Down Playwright

Make actions more visible:
```bash
pytest tests/e2e/test_real_workflow.py -v -s --slowmo=500
```

This adds 500ms delay between actions.

### 4. Increase Test Duration

Edit the test file and increase sleep times to capture more inference cycles.

## Example Output

```
tests/e2e/test_real_workflow.py::test_full_video_analysis_workflow
🎬 Starting full video analysis workflow test...
   This test will take ~30 seconds and create a video recording
   ✅ Page loaded
   📹 Waiting for video stream to start...
   ✅ Video element visible
   ✅ WebSocket connected (GPU stats visible)
   🤖 Waiting for VLM analysis (up to 20 seconds)...
   ✅ VLM analysis detected!
   📊 Monitoring analysis updates for 10 seconds...
   GPU: 100.0%
   🔄 Analysis updated at 2s
   GPU: 87.5%
   GPU: 92.1%
   🔄 Analysis updated at 5s
   GPU: 100.0%
   GPU: 73.2%
   GPU: 88.9%
   🔄 Analysis updated at 8s
   GPU: 95.4%

   📈 Summary:
      - Total runtime: ~30 seconds
      - Analysis updates detected: 3
   📸 Screenshot saved: test-results/workflow-final-state.png
   ✅ Full workflow test completed successfully!
PASSED                                               [100%]

============ 1 passed in 32.45s ============
```

## Next Steps

Once these tests work locally:

1. Run them regularly during development
2. Check the videos to see actual usage
3. Add more assertions based on the UI
4. Customize for specific VLM responses
5. Consider adding tests for edge cases (network loss, model errors, etc.)

