# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Jetson Docker launcher:** Detect an unregistered NVIDIA runtime before starting Jetson Orin containers and show the JetPack repair steps.

---

## [0.4.0] - 2026-03-02

**Cloud deployment, debug payloads, and docs**

### Added
- **Multi-session support**: Per-session VLM state and WebSocket routing so multiple tabs/users get isolated streams and config (cloud-friendly).
- **Cloud deployment config overrides**: Environment variables `LIVE_VLM_API_BASE`, `LIVE_VLM_DEFAULT_MODEL`, and `LIVE_VLM_PROCESS_EVERY` override default API base URL, default model, and frame processing interval (e.g. `LIVE_VLM_PROCESS_EVERY=150` for ~5 s interval to reduce API quota; `LIVE_VLM_API_BASE=https://integrate.api.nvidia.com/v1` and `LIVE_VLM_DEFAULT_MODEL=google/gemma-3-4b-it` for NVIDIA API Catalog). Documented in Docker guide with `-e` / `--env-file` examples.
- **Debug payloads**: In Settings (gear) → Debug: toggles for **Show request payload** and **Show response payload**. Collapsible request JSON (under prompt) and response JSON (under VLM result) for debugging how image and prompt are sent and what the API returns.

### Fixed
- **Server startup**: Resolved `UnboundLocalError` for `os` when using env overrides (removed redundant `import os`/`import sys` inside `main()`).

### Changed
- **Server config**: `server_config` now includes `process_every` so the UI shows the server default frame interval on connect.
- **README**: Ollama on Jetson Thor — recommend upgrading to latest Ollama instead of pinning to 0.12.9; link to troubleshooting for workarounds if needed.

---

## [0.3.0] - 2026-03-02

**UI upgrade and robotics-oriented prompts**

### Added
- **Video overlay controls (play / stop)**:
  - Big green PLAY button centered on video; animates to top-left and fades when streaming starts
  - Small red STOP button in top-left while streaming (higher opacity for visibility)
  - Sidebar start/stop replaced by overlay flow for cleaner UX
- **Fullscreen mode**: Toggle fullscreen on the video card with VLM output overlay; shrink and mirror buttons remain clickable (z-index fix)
- **Robotics-oriented prompt preset**: "Robot Navigation (Simple)" system prompt—describe scene and output 5 navigation commands (`linear_x`, `angular_z`) with reasons, e.g. for bathroom-finding or similar tasks

### Fixed
- **Model initialization race condition**: Auto-selected model is sent to server as soon as WebSocket connects so VLM processing starts without manually re-selecting the model
- **MediaStreamError on stop**: Track end when user stops is handled as normal shutdown (logged at DEBUG only, no error/traceback)
- **Fullscreen controls**: Shrink (minimize) and Mirror buttons stay above the VLM overlay and remain clickable in fullscreen
- **Jetson Thor Docker** ([#14](https://github.com/NVIDIA-AI-IOT/live-vlm-webui/issues/14)): `start_container.sh` now uses `--runtime=nvidia` instead of `--gpus all` on Jetson (Thor and Orin) so containers start correctly

### Changed
- **WebRTC**: Wait for ICE gathering to complete before sending offer (reduces stuck "checking" connections)
- **Troubleshooting**: New "WebRTC connection issues" section (ICE stuck, firewall, STUN, verification steps)
- **Scripts**: `start_server.sh` suggests `kill -9` when port is in use

---

## [0.2.1] - 2025-11-13

### Fixed
- **Version string fix**: Updated `__version__` in `__init__.py` to match package version
  - The `live-vlm-webui --version` command now correctly displays 0.2.1
  - This fixes an issue where v0.2.0 showed 0.1.1 in the version command
- **Test infrastructure**: Fixed pytest-asyncio event loop conflicts with AioHTTPTestCase
  - Changed `asyncio_mode` from `strict` to `auto` in `pyproject.toml`
  - Removed conflicting `event_loop` fixture from `conftest.py`
  - All integration/unit/performance tests now pass when run together
  - Note: E2E tests should be run separately to avoid event loop conflicts

### Changed
- **Documentation**: Consolidated release documentation into single `releasing.md` file
  - Merged `RELEASING.md` content into `releasing.md` and removed redundant file
  - Added critical emphasis on updating `__init__.py` version throughout release process
  - Added detailed test execution instructions to avoid event loop conflicts
  - Updated release checklist with version verification steps

---

## [0.2.0] - 2025-11-13

**New Beta Feature: RTSP IP Camera Support + UI/UX Improvements**

### Added (Beta Features)

#### 🧪 RTSP IP Camera Support (Beta)
- **Status**: Beta - Limited Hardware Testing
- **Tested Hardware**: Reolink RLC-811A (1080p H.264)
- **Features**:
  - Stream video from RTSP IP cameras for continuous monitoring
  - Switch between webcam and RTSP camera in UI
  - Manual RTSP URL configuration with test connection
  - Auto-reconnection on stream drops
  - Support for H.264, H.265, and MJPEG codecs
- **Use Cases**:
  - Pool safety monitoring (child drowning detection)
  - Home surveillance with VLM intelligence
  - Elder care (fall detection)
  - Pet monitoring
  - Security camera analysis
- **Documentation**: Complete setup guide at `docs/usage/rtsp-ip-cameras.md`
- **Known Limitations**:
  - Limited camera compatibility testing (only Reolink tested)
  - Single stream per session
  - No video preview in UI (backend processing only)
  - CPU-based video decoding
- **Community Help Needed**:
  - Test with your IP camera brand/model and report results
  - Help expand the tested hardware compatibility list
  - Report issues on GitHub: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/issues

### Technical Details (RTSP)
- **Backend**: Added `RTSPVideoTrack` class for RTSP stream handling (aiortc + FFmpeg)
- **Frontend**: UI selector to switch between "Webcam" and "RTSP Stream" modes
- **Configuration**: RTSP URL input with optional test connection
- **Error Handling**: Connection failures, stream drops, auto-reconnection
- **Security**: Credentials sanitized in server logs

### Documentation
- Added `docs/usage/rtsp-ip-cameras.md` - Comprehensive RTSP setup guide
  - Quick start with example URLs (Reolink, Hikvision, Dahua, etc.)
  - Use case examples with prompt templates
  - Troubleshooting common issues
  - Performance benchmarks
  - Security and privacy considerations
  - Testing without physical camera (FFmpeg, MediaMTX)
- Updated README.md with Beta feature notice
- Added tested hardware compatibility table

### Added (UI/UX Improvements)

#### 🎨 Theme Support
- **OS Dark/Light Mode Preference**: Automatically detects and honors system theme preference
  - Uses `prefers-color-scheme` media query detection
  - Theme toggle cycles through: Auto → Light → Dark → Auto
  - Manual overrides saved to localStorage
  - Dynamic theme switching when OS preference changes
  - Visual indicator shows current mode (Monitor icon for Auto, Sun for Light, Moon for Dark)

#### 📝 Markdown Rendering
- **Markdown Support in VLM Output**: Render formatted markdown responses from VLMs
  - Toggle button in top-right corner of result balloon
  - Supports headers, lists, code blocks, tables, blockquotes, links, bold, italic
  - Markdown enabled by default for better readability
  - HTML sanitization with DOMPurify for security
  - Theme-aware styling for all markdown elements
  - Button persists during streaming updates

#### 📋 Copy to Clipboard
- **Copy Button**: Transparent overlay button in lower-right corner of result balloon
  - One-click copy of generation results
  - Works with both markdown and plain text modes
  - Visual feedback with checkmark animation
  - Copies raw text (not HTML) for easy sharing

### Changed
- Improved warning color contrast (orange) for better readability on dark backgrounds
- Added warning and error color variables for light theme consistency

### Future Enhancements (Post-Beta)
- Multi-camera support (grid view)
- Video preview in UI for RTSP streams
- Hardware-accelerated video decoding (NVDEC on Jetson)
- ONVIF camera auto-discovery
- Camera preset management
- Motion detection triggers
- Recording/snapshot on AI-detected events

---

## [0.1.1] - 2025-11-12

**Bug Fixes and Documentation Improvements**

### Fixed
- **WSL2 GPU monitoring resilience**: Added robust error handling for intermittent NVML GPU access issues in WSL2 environments
  - Prevents crashes when GPU temporarily unavailable
  - Gracefully falls back when GPU access is lost
  - Improves reliability on Windows Subsystem for Linux

### Added
- **Comprehensive VLM documentation**: Complete model catalog with verified NVIDIA API models
  - Added `docs/usage/list-of-vlms.md` with 16 verified NVIDIA API Catalog models
  - Corrected vision capabilities for gemma3 and llava models
  - Detailed guidance on text-only vs vision-capable models
  - Examples and troubleshooting for common model selection issues

### Documentation
- Added Windows WSL usage guide (`docs/usage/windows-wsl.md`)
- Updated TODO tracker with v0.1.1 status
- Improved troubleshooting documentation

---

## [0.1.0] - 2025-11-09

**First PyPI Release** 🎉

This is the initial public release of Live VLM WebUI - a real-time vision language model interface with WebRTC video streaming and live GPU monitoring.

### Added

#### Core Functionality
- **WebRTC video streaming** with live VLM analysis overlay
- **Real-time VLM integration** supporting multiple backends:
  - Ollama (with auto-detection) ✅ Tested
  - vLLM (with auto-detection) ⚠️ Partially tested
  - SGLang (with auto-detection) ⚠️ Untested - has auto-detection but not validated
  - NVIDIA API Catalog (fallback)
  - OpenAI API (configurable)
- **Live system monitoring** with real-time updates:
  - GPU utilization and VRAM usage
  - CPU utilization and RAM usage
  - Inference latency tracking (last, average, total count)
  - Sparkline charts for historical trends
- **Configurable VLM settings** via WebSocket:
  - Model selection
  - Custom prompts
  - Frame processing interval
  - Max tokens

#### Platform Support
- **Multi-platform GPU monitoring**:
  - NVIDIA GPUs via NVML (PC, workstations, DGX systems)
  - Apple Silicon Macs (via powermetrics)
  - NVIDIA Jetson Orin (via jetson-stats/jtop)
  - NVIDIA Jetson Thor (via jetson-stats/jtop with nvhost_podgov fallback)
  - CPU-only fallback for systems without GPUs
- **Platform-specific product detection and display**:
  - Jetson AGX Orin Developer Kit
  - Jetson Orin Nano Developer Kit
  - Jetson AGX Thor Developer Kit
  - DGX Spark (ARM64 SBSA)
  - Mac (Apple Silicon)
  - Generic PC/Workstation display

#### Security & Network
- **Automatic SSL certificate generation**:
  - Self-signed certificates auto-created on first run
  - Stored in OS-appropriate config directory (~/.config/live-vlm-webui/ on Linux)
  - Fail-fast mechanism if openssl not available
  - HTTPS required for WebRTC camera access
- **Port conflict detection** with helpful error messages
- **Network interface detection** for easy access URL display

#### Installation & Deployment
- **PyPI package** with modern PEP 621 structure:
  - Source layout (src/live_vlm_webui/)
  - Entry points: `live-vlm-webui` (start), `live-vlm-webui-stop` (stop)
  - Platform-agnostic wheel (pure Python)
- **Docker support** with multi-arch images:
  - Multi-arch base image (linux/amd64, linux/arm64)
  - Jetson Orin optimized image (JetPack 6.x / L4T R36)
  - Jetson Thor optimized image (JetPack 7.x / L4T R38+)
  - Mac development image
- **GitHub Actions CI/CD**:
  - Automated Docker image builds and publishing to GHCR
  - Python wheel building and artifact generation
  - Integration and unit tests
  - Code formatting and linting checks

#### Testing & Quality Assurance
- **Automated testing suite** with GitHub Actions CI:
  - **Unit tests**: Python 3.10, 3.11, 3.12 compatibility
  - **Integration tests**: Server startup, WebSocket connections, static file serving
  - **Performance tests**: Benchmarking and metric tracking
  - **Code coverage**: Tracked via Codecov
- **Code quality checks**:
  - Black (code formatting)
  - Ruff (linting)
  - mypy (type checking)
- **E2E workflow tests** (local only):
  - Real video input via Chrome fake device
  - Actual VLM inference with test video
  - Full WebRTC pipeline validation
  - Playwright-based browser automation
  - Note: Requires GPU, Ollama, and browsers (not run in CI)
- **Test documentation**:
  - Testing quickstart guide (docs/development/testing-quickstart.md)
  - E2E workflow testing guide (tests/e2e/real_workflow_testing.md)
  - Test fixtures and utilities

#### Scripts & Tools
- **start_server.sh** - Intelligent server startup script:
  - Virtual environment detection and activation
  - Package installation verification with helpful instructions
  - Port availability checking
  - Jetson platform detection with Docker recommendation
- **start_container.sh** - Docker container launcher:
  - Platform auto-detection (x86_64, ARM64, Jetson variants)
  - GPU runtime configuration (CUDA, Jetson)
  - Existing container detection and restart
- **stop_container.sh** - Docker container management
- **generate_cert.sh** - Manual SSL certificate generation (now automated in Python)

#### Documentation
- **Comprehensive README.md** with:
  - Quick start guides for different platforms
  - Dedicated Jetson installation instructions (pip and Docker)
  - Development setup guide
  - Docker usage examples
  - Contributing guidelines
- **Troubleshooting guide** (docs/troubleshooting.md):
  - Installation issues (setup.py not found, ModuleNotFoundError)
  - Jetson-specific problems (Python version, externally-managed-environment, pip command)
  - jetson-stats installation on Thor (GitHub install, service setup, pipx inject)
  - SSL certificate issues
  - Common runtime errors
- **Developer documentation**:
  - Release process guide (docs/development/releasing.md)
  - Release checklist (docs/development/release-checklist.md)
  - Testing quickstart (docs/development/testing-quickstart.md)
  - E2E workflow testing guide (tests/e2e/real_workflow_testing.md)
  - UI enhancement ideas (docs/development/ui_enhancements.md)
  - TODO tracker (docs/development/TODO.md)
- **Setup guides**:
  - Docker Compose details (docs/setup/docker-compose-details.md)

### Changed
- **Project structure** reorganized for PyPI compatibility:
  - Source code moved to src/live_vlm_webui/
  - Static files (HTML, CSS, JS, images) bundled in package
  - Modern pyproject.toml-based configuration
- **SSL certificate storage** moved to OS-appropriate locations:
  - Linux/Jetson: ~/.config/live-vlm-webui/
  - macOS: ~/Library/Application Support/live-vlm-webui/
  - Windows: %APPDATA%\live-vlm-webui\
  - No more certificate clutter in current working directory
- **Static file serving** improved to work correctly for both:
  - Development mode (pip install -e .)
  - Production mode (pip install from wheel)
- **Jetson Orin Nano product name** cleaned up for display:
  - "NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super"
  - → "NVIDIA Jetson Orin Nano Developer Kit"

### Fixed
- **Port 8090 conflict detection** now uses reliable Python socket binding test
- **Image serving** for pip wheel installations (GPU product images now correctly bundled)
- **Docker builds** with new package structure (pip install -e . instead of requirements.txt)
- **Virtual environment detection** in start_server.sh (handles both .venv and venv)
- **Package installation verification** before server start
- **Jetson Thor pip installation** with Python 3.12 / PEP 668:
  - Recommend pipx for live-vlm-webui
  - Document jetson-stats installation from GitHub
  - Provide complete jetson-stats setup (service + pipx inject)
- **`live-vlm-webui-stop` command** now properly exposed as pip entry point
  - Was documented but missing from pyproject.toml
  - Now available after pip installation for graceful server shutdown

### Tested On
- ✅ x86_64 PC (Linux Ubuntu 22.04)
- ✅ NVIDIA DGX Spark (ARM64 SBSA)
- ✅ macOS (Apple Silicon M-series)
- ✅ NVIDIA Jetson AGX Orin (ARM64 L4T R36 / JetPack 6.x)
- ✅ NVIDIA Jetson AGX Thor (ARM64 L4T R38.2 / JetPack 7.0)

### Dependencies
- Python ≥ 3.10 (3.12 for Jetson Thor)
- aiohttp ≥ 3.9.5 - Async HTTP server and WebSocket support
- aiortc ≥ 1.10.0 - WebRTC implementation
- opencv-python ≥ 4.8.0 - Video frame processing
- numpy ≥ 1.24.0 - Image array manipulation
- openai ≥ 1.0.0 - VLM API client (OpenAI-compatible)
- psutil ≥ 5.9.0 - System resource monitoring
- nvidia-ml-py ≥ 11.495.46 - NVIDIA GPU monitoring (optional)
- pynvml ≥ 11.0.0 - NVIDIA GPU monitoring (legacy, optional)
- jetson-stats (Jetson only, optional) - Jetson-specific monitoring

### Known Limitations
- **Single-user/single-session architecture**: Multiple users share VLM state and see each other's outputs
  - Workaround: Deploy multiple instances on different ports
  - Future: Multi-user support planned (see TODO.md)
- **Jetson jtop dependency**: jetson-stats complicates pip installation on Jetson
  - Thor requires GitHub installation + service setup + pipx inject
  - Future: Direct GPU stats without jtop planned
- **WSL support**: Not yet tested on Windows Subsystem for Linux
- **Browser compatibility**: Tested on Chrome, Firefox, Edge (Safari may have WebRTC limitations)

### Known Issues
- **Mac: mlx-vlm dependency conflict warning** (non-blocking)
  - pip may show transformers version conflict during installation
  - Impact: Warning only - package installs and runs correctly
  - See [troubleshooting guide](./docs/troubleshooting.md) for details

### Security Notes
- Uses self-signed SSL certificates by default (browser security warnings expected)
- No authentication or rate limiting (suitable for local/trusted network use only)
- Not recommended for public internet deployment without additional security measures

---

## Project Information

**Repository**: https://github.com/NVIDIA-AI-IOT/live-vlm-webui
**PyPI Package**: https://pypi.org/project/live-vlm-webui/
**Docker Images**: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/pkgs/container/live-vlm-webui
**License**: Apache License 2.0

---

[Unreleased]: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/releases/tag/v0.1.0
