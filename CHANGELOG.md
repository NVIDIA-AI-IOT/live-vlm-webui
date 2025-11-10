# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Nothing yet - this is the first release!

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

[Unreleased]: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/releases/tag/v0.1.0

