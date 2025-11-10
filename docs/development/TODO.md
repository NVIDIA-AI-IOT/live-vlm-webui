# TODO Tracker for live-vlm-webui

**Last Updated:** 2025-11-09 (Pre v0.1.0 PyPI release)

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

### 🔴 BLOCKING (Must complete before v0.1.0)

- [x] **Create CHANGELOG.md** - Required for release notes ✅
  - Location: `/home/lekto/live-vlm-webui/CHANGELOG.md`
  - Format: [Keep a Changelog](https://keepachangelog.com/)
  - Content: Document all changes since project start → v0.1.0

- [ ] **Update README.md "PyPI package coming soon!" warning**
  - Remove or update: `> **PyPI package coming soon!** Works on all platforms including Jetson (requires openssl).`
  - Replace with: Link to PyPI package page once published

- [ ] **Final version verification**
  - Confirm `pyproject.toml` version is `0.1.0`
  - All references to version numbers are consistent

- [ ] **Test wheel on all target platforms (final verification)**
  - [ ] x86_64 PC (Linux Ubuntu)
  - [ ] NVIDIA DGX Spark (ARM64 SBSA)
  - [ ] macOS (Apple Silicon)
  - [ ] Jetson Thor (ARM64 L4T R38+)
  - [ ] Jetson Orin (ARM64 L4T R36)

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

- [ ] **Download button for recordings** (UI)
  - Mentioned in README line 191: "Download button (coming soon)"
  - Priority: Low (nice-to-have, not essential)

- [ ] **AMD GPU monitoring support** (Platform Support)
  - Priority: Low (expand platform support, not near-term)
  - Requires: ROCm/rocm-smi integration
  - Note: Removed "coming soon" from README to avoid incorrect expectations

- [ ] **WSL support verification** (Platform Support)
  - Currently not even tested on WSL
  - Would be nice to test with WSL with Ollama natively running on Windows
  - Priority: Medium

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

