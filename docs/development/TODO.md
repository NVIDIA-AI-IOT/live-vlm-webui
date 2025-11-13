# TODO Tracker for live-vlm-webui

**Last Updated:** 2025-11-12 (Post v0.1.0, preparing v0.1.1)

This document consolidates all TODO items from across the codebase, categorized by priority and status.

---

## 🚨 Critical for v0.1.0 PyPI Release

### ✅ COMPLETED (Ready for release)

- [x] Create CHANGELOG.md with release notes
- [x] Python wheel builds correctly
- [x] Wheel tested on multiple platforms (x86_64 Linux, ARM64 DGX Spark, macOS, Jetson Thor, Jetson Orin)
- [x] Docker images build and run correctly
- [x] SSL certificate auto-generation working
- [x] SSL certificates stored in app config directory (not CWD)
- [x] Static images (GPU product images) properly bundled in wheel
- [x] Documentation updated for pip installation
- [x] Jetson-specific installation instructions (Thor + Orin)
- [x] GitHub Actions workflow for wheel building
- [x] Integration tests passing
- [x] All linter/formatting checks passing

### ✅ COMPLETED - v0.1.0 Released! (2025-11-10)

All blocking items for v0.1.0 have been completed:

- [x] **Create CHANGELOG.md** - ✅ Completed
- [x] **Update README.md** - ✅ PyPI installation documented
- [x] **Final version verification** - ✅ Version 0.1.0 confirmed
- [x] **Test wheel on platforms** - ✅ Tested during development
- [x] **PyPI package published** - ✅ Available: `pip install live-vlm-webui`
- [x] **GitHub release created** - ✅ v0.1.0 published
- [x] **Docker images** - ✅ `latest` tags available
- [x] **Apache 2.0 LICENSE** - ✅ Added (2025-11-10)
- [x] **License headers** - ✅ All source files updated
- [x] **OSRB approval** - ✅ VP: 11/05, Final: 11/06/2025
- [x] **Troubleshooting doc** - ✅ Added section on text-only vs vision models (2025-11-10)

---

## 🔧 v0.1.1 Improvements (Future Release)

### Completed for v0.1.1

- ✅ **WSL2 GPU monitoring fix** (`ec277db`) - Resilient NVML error handling for intermittent WSL2 GPU access issues
- ✅ **Comprehensive VLM documentation** (`64ec81e`) - Complete model catalog with 16 verified NVIDIA API models, corrected vision capabilities for gemma3/llava

### Release Process Improvements

- ✅ **Add versioned Docker image tags via git tags** (`191a95a`, `ea8b857`)
  - **Completed**: Interactive version selection with `--version`, `--list-versions`, `--skip-version-pick` flags
  - **Hybrid fetching**: GHCR API (authenticated) → Releases API (public) → Fallback list
  - **Public API support**: Works without GITHUB_TOKEN (60 req/hour via Releases API)
  - **Testing**: `--simulate-public` flag to test unauthenticated experience
  - **Benefit**: Users can select specific versions interactively or via CLI flags
  - **Note**: Supports both X.Y and X.Y.Z version formats (e.g., `0.1`, `0.1.1`)
  - **Usage**:
    ```bash
    ./scripts/start_container.sh                    # Interactive picker
    ./scripts/start_container.sh --version 0.1.1    # Specific version
    ./scripts/start_container.sh --list-versions    # Show all versions
    ./scripts/start_container.sh --simulate-public  # Test public API
    ```

- ✅ **Document release process for future releases** (`191a95a`)
  - Created `docs/development/RELEASING.md` with step-by-step guide
  - Includes: version bump, changelog, git tag, GitHub release, PyPI upload, Docker verification
  - Standard operating procedure for maintainers

---

## 📋 Post-Release v0.1.0 (Can defer)

### Documentation

- [ ] **Remove "coming soon" notes from README**
  - Line 38: PyPI package warning
  - Line 191: Download button mention

- [ ] **Add CHANGELOG.md maintenance**
  - Add [Unreleased] section for future changes
  - Document ongoing changes as they're made

- [ ] **Update troubleshooting.md**
  - Monitor user feedback for common installation issues
  - Add new platform-specific issues as discovered

### Features & Enhancements

- [x] **RTSP IP Camera Support** (Feature - HIGH VALUE) ✅ **COMPLETED in feature/rtsp-camera-support**
  - **Status**: Core functionality implemented, ready for testing
  - **What it does**: Process video from RTSP-compatible IP cameras instead of just webcams
  - **Use cases**:
    - Home safety monitoring (pool drowning detection, elder care fall detection)
    - Security surveillance with AI analysis
    - Pet/baby monitoring
    - Retail customer counting
    - Warehouse safety monitoring
    - Use Jetson/PC as dedicated edge AI surveillance appliance
  - **Implementation**:
    - ✅ `RTSPVideoTrack` class for RTSP stream ingestion with auto-reconnection
    - ✅ Backend endpoints: `/api/rtsp/start`, `/api/rtsp/stop`, `/api/rtsp/status`
    - ✅ Frontend UI: Input source selector (Webcam vs RTSP)
    - ✅ RTSP URL input with connection testing
    - ✅ Error handling and user-friendly messages
    - ✅ Comprehensive documentation with examples
  - **Documentation**: `docs/usage/rtsp-ip-cameras.md`
  - **Components modified**:
    - `src/live_vlm_webui/rtsp_track.py` (new)
    - `src/live_vlm_webui/server.py` (RTSP endpoints added)
    - `src/live_vlm_webui/static/index.html` (UI controls added)
  - **Known limitations**:
    - Single stream per session (multi-stream planned for future)
    - No video preview in UI (backend processing only)
    - CPU-based decode (hardware acceleration planned)
  - **Next steps**:
    - [ ] Unit tests for RTSPVideoTrack
    - [ ] Real IP camera testing (Reolink, Hikvision, etc.)
    - [ ] Performance benchmarking
    - [ ] Multi-stream support (future)
    - [ ] Video preview in UI via server-sent events (future)
    - [ ] Hardware-accelerated decode with NVDEC on Jetson (future)
  - **Branch**: `feature/rtsp-camera-support`
  - **Target release**: v0.2.0
  - Priority: High (multiple user requests, compelling use cases)
  - Effort: ~2-3 weeks (completed in initial implementation)

- [ ] **Markdown rendering in VLM output** (UI/UX - MEDIUM PRIORITY)
  - **Current state**: VLM responses display as plain text, raw markdown shown
  - **Issue**: Many VLMs output markdown formatting (bold, lists, code blocks, tables, etc.)
    - Example: gemma3:4b can output markdown formatted responses
    - Raw markdown like `**bold**` or `- bullet` is shown literally
  - **Goal**: Render markdown in VLM output balloon for better readability
  - **Implementation**:
    - Add markdown renderer library (e.g., marked.js, markdown-it)
    - Render VLM response HTML from markdown
    - Preserve formatting: bold, italic, lists, code blocks, tables
    - Apply appropriate CSS styling to match UI theme
    - Add toggle option: "Render Markdown" vs "Show Raw Text"
  - **Benefits**:
    - Much better readability for formatted responses
    - Leverage VLM's ability to structure output
    - Code blocks rendered with syntax highlighting
    - Tables displayed properly (useful for data analysis responses)
  - **Considerations**:
    - Sanitize HTML to prevent XSS attacks
    - Handle edge cases where VLM outputs partial/broken markdown
    - Maintain performance (render on-the-fly as text streams in)
    - Option to copy raw markdown text for external use
  - **UI changes**:
    - Add "Markdown" toggle button in settings or output area
    - Visual indicator when markdown mode is active
    - Fallback to plain text if markdown parsing fails
  - Priority: Medium (nice quality-of-life improvement, enhances VLM output usability)
  - Effort: ~4-6 hours (add library, implement rendering, styling, testing)

- [ ] **Honor OS/environment dark/light mode preference** (UI/UX - MEDIUM PRIORITY)
  - **Current state**: UI uses a fixed dark theme
  - **Goal**: Automatically detect and respect OS-level dark/light mode preference
  - **Implementation**:
    - Use CSS `prefers-color-scheme` media query
    - Detect system preference via JavaScript (`window.matchMedia`)
    - Support dynamic theme switching when OS preference changes
    - Optional: Add manual theme toggle override in settings
  - **Benefits**:
    - Better UX for users with light mode preference
    - Respects accessibility settings
    - Modern web app best practice
  - Priority: Medium
  - Effort: ~2-4 hours (CSS variables, theme switching logic, testing)

- [ ] **Jetson GPU stats without jtop dependency** (Platform Support - HIGH PRIORITY)
  - **Current issue**: jtop (jetson-stats) requirement complicates pip wheel installation
  - **Goal**: Direct GPU utilization and VRAM consumption retrieval
  - **Approaches**:
    - Wait for future L4T release with updated NVML support for Thor
    - Investigate lower-level interfaces (sysfs, tegrastats alternatives)
    - Direct GPU metrics access without Python dependencies
  - **Benefits**:
    - Simpler pip installation (no jetson-stats complexity)
    - More efficient monitoring
    - Better user experience for pip-based installs
  - **Additional feature**: Stacked memory consumption graph for UMA systems
    - Jetson and DGX Spark use Unified Memory Architecture
    - Current sparklines don't show memory composition well
    - Consider chart library upgrade for better UMA visualization
  - Priority: High (significantly improves Jetson pip installation experience)

- [ ] **Multi-user/multi-session support** (Architecture - critical for cloud hosting)
  - **Current limitation**: Single-user, single-session architecture
    - If accessed by multiple users, they share same VLM service instance and see each other's outputs
    - Settings changes affect all connected users
    - Only one VLM inference at a time (sequential processing)
  - **Required for**: Cloud deployment, team demos, production use
  - **Implementation levels**:
    - **Level 1 (Basic)**: Session management with isolated VLM state per user
      - Session IDs for WebSocket connections
      - Per-session VLM service instances
      - Targeted message broadcasting (not broadcast to all)
      - Effort: ~8-12 hours
      - Supports: 10-20 concurrent users
    - **Level 2 (Efficient)**: Shared VLM backend with request queue
      - Request queue with session context
      - Fair scheduling and rate limiting per user
      - Batching for efficiency
      - Effort: ~16-24 hours
      - Supports: 20-50 concurrent users
    - **Level 3 (Enterprise)**: Distributed scalable architecture
      - Stateless frontend servers
      - Redis/database for session state
      - Separate VLM service layer with load balancing
      - Authentication & authorization
      - Multi-tenancy support
      - Effort: ~4-8 weeks (major rewrite)
      - Supports: 100+ concurrent users
  - **Current workaround**: Deploy multiple independent instances on different ports
    - Run separate Python processes or containers, each on different port
    - Works without code changes, suitable for 5-10 users
  - Priority: Med (required if to host this web UI on some public instance)

- [ ] **Hardware-accelerated video processing on Jetson** (Performance)
  - Location: `src/live_vlm_webui/video_processor.py:19`
  - Description: Implement NVMM/VPI color space conversion
  - Priority: Medium (optimization, not blocking)
  - Benefit: Reduce CPU load during video processing

- [ ] **Display detailed VLM inference metrics** (UI/Metrics)
  - **Current state**: Only showing total latency (ms), avg latency, inference count
  - **Goal**: Display detailed breakdown of VLM inference phases
  - **Background**: VLM inference has two distinct phases:
    - **Prefill phase** (prompt processing): Image encoding + prompt → KV cache population
      - Duration: 500-2000ms for VLMs (image encoding is expensive)
      - User experience: Long pause before any text appears
    - **Decode phase** (token generation): Generate tokens one-by-one
      - Duration: Depends on response length and GPU speed
      - User experience: Text appears word-by-word after prefill
  - **Metrics to display**:
    1. **Prefill time** (ms) - "Time to first token" - shows image processing overhead
    2. **Decode speed** (tokens/sec) - "Generation speed" - shows GPU efficiency
    3. **Total time** (ms) - Already showing as "Latency"
    4. **Tokens generated** - Response length
    5. **Vision tokens** - Number of tokens from image (optional, technical detail)
  - **Implementation**:
    - **Ollama**: API returns rich metrics in response object
      ```python
      {
        "prompt_eval_count": 577,        # text + vision tokens
        "prompt_eval_duration": 1500000000,  # nanoseconds (prefill)
        "eval_count": 25,                # generated tokens
        "eval_duration": 1250000000,     # nanoseconds (decode)
      }
      # Calculate:
      # prefill_ms = prompt_eval_duration / 1e6
      # decode_tokens_per_sec = eval_count / (eval_duration / 1e9)
      ```
    - **vLLM**: Check if response object includes timing metadata (may not be available via OpenAI-compatible API)
      - Open WebUI likely has same limitation
      - May need to use vLLM-specific endpoint or parse response headers
    - **Other backends**: Show "N/A" gracefully if metrics unavailable
  - **UI changes**:
    - Add metrics to inline display: "Prefill: Xms | Gen: Y tok/s | Tokens: Z"
    - Update WebSocket `vlm_response` message format
    - Update `vlm_service.py` to extract and return detailed metrics
    - Consider collapsible "Advanced Metrics" section for technical details
  - **Note**: Need to detect backend type or parse response metadata structure
  - Priority: Medium (valuable for performance analysis and model comparison)
  - Benefit: Users can see where time is spent (image encoding vs text generation), essential for r/LocalLLaMA community

- [ ] **Multi-frame temporal understanding** (Features - HIGH IMPACT)
  - **Goal**: Enable VLM to understand motion, actions, and changes over time
  - **Current limitation**: Each frame is analyzed independently with no temporal context
  - **Use cases**:
    - Action recognition ("person is waving", "car is turning left")
    - Motion detection ("object is moving from left to right")
    - Change detection ("door just opened", "person entered the frame")
    - Temporal reasoning ("person picked up cup then drank from it")
  - **Technical approaches**:

    **Option 1: Multi-image API (Recommended if supported)**
    - Send multiple frames (e.g., 4-8 frames) in a single API request
    - VLM processes them together with temporal understanding
    - **Supported by**:
      - ✅ GPT-4V / GPT-4o (OpenAI) - supports multiple images in one prompt
      - ✅ Claude 3 (Anthropic) - supports multiple images
      - ✅ Gemini (Google) - native video understanding
      - ❓ Ollama - depends on underlying model (need to test)
      - ❓ vLLM - depends on model architecture
    - **API format** (OpenAI-compatible):
      ```python
      messages = [{
        "role": "user",
        "content": [
          {"type": "text", "text": "What action is happening across these frames?"},
          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},  # frame 1
          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},  # frame 2
          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},  # frame 3
          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},  # frame 4
        ]
      }]
      ```
    - **Pros**: Native temporal understanding, best quality
    - **Cons**: Requires backend support, higher latency, more VRAM

    **Option 2: Grid/collage approach (Universal fallback)**
    - Combine 4-8 frames into a single grid image (e.g., 2x2 or 2x4 layout)
    - Add frame numbers/timestamps as text overlay
    - Send as one image with prompt: "These frames show a sequence over time. Describe what's happening."
    - **Pros**: Works with ANY VLM, simpler implementation
    - **Cons**:
      - Loss of resolution per frame (4 frames = 50% resolution each dimension)
      - Model must understand spatial grid layout
      - Less effective than native multi-image support
    - **Example layout**:
      ```
      [Frame 1: t=0.0s] [Frame 2: t=0.5s]
      [Frame 3: t=1.0s] [Frame 4: t=1.5s]
      ```

    **Option 3: Conversation/sliding window (Advanced)**
    - Maintain conversation history with last N frames
    - Each new frame references previous frames in context
    - Requires conversation state management
    - **Pros**: Can maintain longer temporal context
    - **Cons**: Complex state management, context window limits, may not work well

    **Option 4: Video-native VLMs (Future)**
    - Use models specifically designed for video understanding
    - Examples: LLaVA-Video, Video-LLaMA, Video-ChatGPT, Qwen2-VL
    - These models have temporal attention mechanisms
    - **Pros**: Best temporal understanding
    - **Cons**: Limited model availability, requires backend changes

  - **Implementation plan**:
    1. Add frame buffer to `VideoProcessorTrack` to store last N frames
    2. Add UI toggle: "Temporal Mode" with frame count selection (2-8 frames)
    3. Implement grid stitching as universal fallback (Option 2)
    4. Detect backend capabilities and use multi-image API if available (Option 1)
    5. Add temporal-specific prompts: "Describe the motion/action across these frames"
    6. Update metrics to show "frames per inference" and "temporal window"

  - **UI considerations**:
    - Toggle: "Single Frame" vs "Temporal (4 frames)" vs "Temporal (8 frames)"
    - Frame rate adjustment: Slower frame rate for temporal mode (e.g., 1 FPS instead of 0.5 FPS)
    - Visual indicator showing which frames are being analyzed
    - Latency will be higher (4x frames = ~2-4x longer prefill time)

  - **Performance impact**:
    - **Prefill time**: Increases linearly with frame count (4 frames ≈ 4x prefill time)
    - **VRAM**: Increases with frame count (more vision tokens in KV cache)
    - **Inference rate**: Must be slower (wait for N frames to accumulate)
    - Example: 4 frames @ 1 FPS = 4 seconds of temporal context, ~4-8 sec total latency

  - **Testing needed**:
    - Test Ollama with different vision models (llava, llama3.2-vision) for multi-image support
    - Test vLLM with multi-image capable models
    - Measure latency/VRAM impact with different frame counts
    - Evaluate quality: multi-image API vs grid approach

  - Priority: High (major feature, high user demand for action/motion understanding)
  - Effort: ~2-3 days for basic implementation (grid approach), +1-2 days for multi-image API
  - Benefit: Unlocks entirely new use cases (action recognition, motion tracking, surveillance, robotics)

- [ ] **Download button for recordings** (UI)
  - Mentioned in README line 191: "Download button (coming soon)"
  - Priority: Low (nice-to-have, not essential)

- [ ] **AMD GPU monitoring support** (Platform Support)
  - Priority: Low (expand platform support, not near-term)
  - Requires: ROCm/rocm-smi integration
  - Note: Removed "coming soon" from README to avoid incorrect expectations

- [x] **WSL support verification and fix** (Platform Support)
  - ✅ **Tested and working** on WSL2 with Ubuntu 22.04
  - ✅ GPU inference works (CUDA support confirmed)
  - ✅ Ollama running inside WSL works perfectly
  - ✅ **GPU monitoring fixed!** - WSL2 has intermittent NVML errors, now handled with retry logic
    - Root cause: WSL2's NVML occasionally returns `NVMLError_Unknown` (transient error, not fundamental limitation)
    - Fix: Added automatic retry logic - only disables after 10+ consecutive errors
    - Recovery: Automatically recovers when NVML calls succeed again
    - Behavior: May show 0% briefly during intermittent errors, then recovers
  - ❌ **Doesn't work:** Ollama on Windows + WebUI on WSL (networking issue)
    - Windows Ollama only listens on `127.0.0.1`, not accessible from WSL network
    - Solution: Install Ollama inside WSL instead
  - 📝 **Documentation:** Created comprehensive guide at `docs/usage/windows-wsl.md`
  - 🐛 **Code fix:** `src/live_vlm_webui/gpu_monitor.py` - resilient error handling
  - 📦 **Commit:** `ec277db` - Fix WSL2 GPU monitoring with resilient NVML error handling
  - Priority: Complete and fixed for v0.1.1

- [x] **Comprehensive VLM reference documentation** (Documentation)
  - ✅ **Created:** Complete VLM catalog at `docs/usage/list-of-vlms.md`
  - ✅ **Ollama:** 14+ vision models with size recommendations by hardware
  - ✅ **NVIDIA API:** 16 vision models verified from live API query
    - Includes Google gemma-3 models (4b/12b/27b with vision support)
  - ✅ **OpenAI:** Added GPT-5 (released August 2025), updated model hierarchy
  - ✅ **Anthropic:** Claude 3.5 Sonnet and family
  - ✅ **Corrections:** Fixed vision capability documentation
    - gemma3: Only 4b/12b/27b support vision (270m/1b are text-only)
    - llava: Only 7b/13b support vision (34b is text-only)
  - ✅ **Cross-references:** Added links from README and troubleshooting docs
  - ✅ **Model selection guides:** By use case, hardware, and deployment environment
  - ✅ **Verification guide:** How to distinguish vision vs text-only models
  - 📦 **Commit:** `64ec81e` - Add comprehensive VLM reference guide with verified model lists
  - Priority: Complete and ready for v0.1.1

### Testing

- [ ] **Expand E2E test coverage**
  - Current tests cover basic workflow
  - Add tests for edge cases:
    - [ ] Network interruptions during inference
    - [ ] Model switching edge cases
    - [ ] Camera permission denial handling
    - [ ] VLM API failures/timeouts

- [ ] **Performance benchmarking suite**
  - Standardized tests for video processing throughput
  - VLM inference latency measurements
  - GPU utilization tracking

### Infrastructure

- [ ] **Automated PyPI publishing on GitHub Release**
  - Update `.github/workflows/build-wheel.yml`
  - Configure PyPI Trusted Publishing
  - Document in `docs/development/releasing.md` (partially done)

- [ ] **Code coverage improvement**
  - Current: ~20% (per CI reports)
  - Target: >50% for core modules
  - Focus on `server.py`, `gpu_monitor.py`, `vlm_service.py`

---

## 🎯 Future Roadmap (v0.2.0+)

### Core Functionality

- [ ] **Recording/export functionality**
  - Record analysis results or even with video stream
  - If with video, export video as MP4/webm with annotations
  - Timestamp-based analysis log

- [] **Side-by-side VLMs comparison**
  - Compare two different VLM's outputs side-by-side

- [ ] **Multi-frame support**
  - Option to injest multiple frames from WebRTC to VLM for temporal uderstading

- [ ] **Prompt template addition**
  - User-defined analysis prompts
  - More Prompt templates for common use cases
  - Per-model prompt customization

### Validation

- [ ] **Additional VLM backends**
  - Local
    - VLLM (partically tested)
    - SGLang
    - Local Hugging Face models
  - Clooud
    - OpenAI API (partially done)
    - Anthropic Claude API
    - Azure OpenAI

### Platform Support

- [ ] **Windows native installer**
  - MSI/EXE installer for Windows
  - Bundled Python runtime
  - One-click installation

- [ ] **Raspberry Pi test**
  - Test on RPi 4/5 (if any VLM run on RPi)
  - Document performance characteristics

### UI/UX Improvements

Ideas to be examined documented in `docs/development/ui_enhancements.md`

---

## 📝 Documentation TODOs

### Already Documented (✅)

These are checklists in documentation files, not actual TODOs:

- Release process checklist in `docs/development/release-checklist.md`
- Release workflow in `docs/development/releasing.md`
- Manual testing checklist in `tests/e2e/real_workflow_testing.md`
- Contributing checklist in `CONTRIBUTING.md`

**Note:** These are reference checklists for users/maintainers, not pending tasks.

---

## 🔍 Investigation Needed

### Potential Issues to Monitor

1. **jetson-stats PyPI availability for Thor**
   - Current: Must install from GitHub
   - Monitor: https://github.com/rbonghi/jetson_stats/releases
   - Action: Update docs when Thor support released to PyPI

2. **Python 3.13 compatibility**
   - Currently tested up to Python 3.12
   - Monitor: Dependency compatibility with Python 3.13
   - Action: Test and update `pyproject.toml` when stable

3. **WebRTC browser compatibility**
   - Currently tested: Chrome, Firefox, Edge
   - Safari: May have WebRTC limitations
   - Action: Document browser-specific limitations

---

## ✅ Recently Completed (For Reference)

### Completed in Pre-v0.1.0 Development

- ✅ PyPI package structure (src/ layout)
- ✅ Automated SSL certificate generation
- ✅ Jetson Orin Nano product image display fix
- ✅ Jetson Thor Python 3.12 / PEP 668 support (pipx)
- ✅ Comprehensive Jetson installation documentation
- ✅ GitHub Actions wheel building workflow
- ✅ TestPyPI publication and verification
- ✅ Multi-platform testing (x86_64, ARM64, macOS, Jetson)
- ✅ Docker image fixes for new package structure
- ✅ Static file serving improvements
- ✅ `live-vlm-webui-stop` command
- ✅ Port conflict detection in start script
- ✅ Virtual environment detection and activation
- ✅ Package installation verification in start script
- ✅ Comprehensive troubleshooting documentation
- ✅ Docker multi-arch builds on GitHub Actions (amd64, arm64, Jetson Orin, Jetson Thor, Mac)

---

## 📊 Priority Legend

- 🚨 **Critical**: Blocking PyPI release
- 🔴 **High**: Should complete soon after release
- 🟡 **Medium**: Important but can wait
- 🟢 **Low**: Nice to have, no rush

---

## 🔄 Maintenance Notes

**How to use this document:**

1. **Before each release**: Review and update all sections
2. **During development**: Add TODOs here instead of scattered comments
3. **After completing items**: Move to "Recently Completed" section
4. **Monthly review**: Re-prioritize based on user feedback

**Keep this document synchronized with:**
- Code comments (avoid duplicate TODOs)
- GitHub Issues (for community-reported items)
- CHANGELOG.md (for completed features)

---

**Document Status:** Active tracking document for v0.1.0 → v0.2.0 development cycle
