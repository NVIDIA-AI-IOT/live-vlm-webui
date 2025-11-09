# Testing & Performance Guide

## Overview

The Live VLM WebUI project has a comprehensive testing strategy that includes:
- **Unit tests** - Test individual components in isolation
- **Integration tests** - Test component interactions
- **End-to-end tests** - Test complete workflows with real browsers
- **Performance tests** - Ensure real-time constraints are met

## Testing Strategy: CI vs Local Development

### Two Different Goals

1. **CI/CD (GitHub Actions)** - Fast, reliable, catch breaking changes
   - **Goal:** Prevent broken code from merging
   - **Constraints:** Must be fast (<5 min total), no special hardware required
   - **Run:** On every commit/PR
   - **Tests:** Unit tests, fast integration tests

2. **Local Development** - Comprehensive, catch regressions
   - **Goal:** Verify everything works end-to-end before releases
   - **Constraints:** Can be slow, can use GPU, developer runs as needed
   - **Run:** Before releases, when making major changes, manual verification
   - **Tests:** All tests including E2E with video recording

### Current Tests Classification

| Test | Type | Duration | Requires | Recommended For |
|------|------|----------|----------|-----------------|
| `test_gpu_monitor_real.py` | Unit | <1s | None | ✅ **CI + Local** |
| `test_server.py` | Integration | <1s | Test server | ✅ **CI + Local** |
| `test_gpu_monitor_performance.py` | Performance | ~5s | GPU (NVIDIA) | 🏠 **Local only** |
| `test_real_workflow.py` | E2E | ~45-60s | GPU, Ollama, 1GB+ video | 🏠 **Local/Pre-release** |

### Why E2E Tests are Currently Local-Only

The `test_real_workflow.py` E2E test is comprehensive but currently too resource-intensive for CI:
- ⏱️ Takes 45-60 seconds (CI should be <5 min total for all tests)
- 🎬 Downloads 151 MB video + converts to 949 MB .y4m format
- 🤖 Requires Ollama with specific models (2-8 GB each) running locally
- 🎥 Records video of the entire workflow (storage costs)
- 🖥️ GPU-specific (GitHub Actions runners don't have NVIDIA GPUs)
- 🔄 Downloads/installs multiple models during test

**This is the comprehensive "smoke test before release"** - thorough verification but too heavy for every commit.

#### Future: CI-Compatible E2E Testing with Cloud APIs

The E2E test **could theoretically run in CI** with these adaptations:

1. **Replace local Ollama with cloud VLM APIs:**
   - Use OpenAI GPT-4V, NVIDIA API Catalog, or Anthropic Claude Vision
   - No GPU or local models required
   - Removes the biggest blocker for CI

2. **VLM-powered test verification:**
   - Playwright already records video in headless mode (CI-compatible)
   - Use a VLM API to analyze the recorded video
   - Ask: "Does this video show: (1) video streaming working, (2) theme switching to light mode at ~15s, (3) settings modal opening and closing, (4) model switching, (5) VLM analysis appearing on screen?"
   - VLM responds with verdict: Pass/Fail + reasoning

3. **Benefits of VLM-based verification:**
   - More robust than DOM assertions (catches visual bugs)
   - Tests the actual user experience (what they see)
   - Easier to maintain (no fragile selectors)
   - Can verify complex workflows with natural language

4. **Remaining considerations:**
   - 💰 Cost: Cloud API calls cost money per test run
   - ⏱️ Speed: Still slower than unit tests (~45-60s)
   - 🔌 Reliability: Depends on cloud API availability
   - 🎥 Storage: Video recordings still consume CI storage

**Trade-off:** Local testing with Ollama is free but requires GPU. Cloud testing works in CI but costs money per run. Choose based on priorities (cost vs automation).

### Recommended Testing Workflow

**Tier 1: CI/CD (Automated, every commit)** ⚡
```bash
# Fast, no special dependencies
pytest tests/unit/ -v                          # ~1 second
pytest tests/integration/ -v -m "not slow"     # ~2 seconds
# Total: ~3-5 seconds ✅
```

**Tier 2: Pre-commit (Local, optional)** 🔧
```bash
# Developers run locally before pushing
pytest tests/ -m "not e2e and not performance" -v
```

**Tier 3: Pre-release (Local, manual verification)** 🚀
```bash
# Full workflow test with video recording before releases
pytest tests/e2e/test_real_workflow.py -v -s
# Creates video recording in test-results/videos/
# Verifies: UI, video streaming, VLM inference, settings, model switching
```

**Tier 4: Performance Monitoring (Local, as needed)** 📊
```bash
# Check for performance regressions
pytest tests/performance/ -v
# Requires: NVIDIA GPU with NVML support
```

## Quick Reference

```bash
# Quick tests (fast, unit tests only)
./scripts/test_quick.sh

# Full test suite
./scripts/run_tests.sh

# With coverage
./scripts/run_tests.sh -c

# Performance tests
./scripts/run_performance_tests.sh

# Pre-commit checks
./scripts/pre_commit_check.sh
```

## Performance Testing Strategy

### Why Performance Testing Matters

For real-time video processing, **timing is critical**:
- At **30fps**, you have only **33.33ms** per frame
- At **60fps**, you have only **16.67ms** per frame

If processing takes longer than the frame budget, you'll drop frames and the user experience degrades.

### Performance Budget Allocation

We allocate time budgets for each component:

```
Frame Time (30fps): 33.33ms
├── Video Decode:      ~5ms
├── Frame Processing:  ~3ms (resize, preprocessing)
├── Frame Encoding:    ~3ms (JPEG encode for WebRTC)
├── Network I/O:       ~10ms
└── Overhead:          ~12ms
                      ──────
Total Pipeline:        ~15ms (excluding VLM inference)

VLM Inference: 100-500ms (async/queued, doesn't block frame processing)
```

### Performance Test Approach

We use a **three-tier approach** to performance testing:

#### 1. Individual Function Tests

Test each function meets its time budget:

```python
@pytest.mark.performance
def test_frame_resize_speed():
    """Frame resize should be < 3ms."""
    metrics = PerformanceMetrics()

    for _ in range(10):
        start = time.perf_counter()
        resized = processor.resize_frame(frame)
        duration_ms = (time.perf_counter() - start) * 1000
        metrics.record("resize", duration_ms)

    stats = metrics.get_stats("resize")

    # Soft warning if exceeds budget
    if stats['mean'] > 3.0:
        print("⚠️  Warning: Exceeds 3ms budget")

    # Hard assertion: must be under frame time
    assert stats['p95'] < 33.33
```

#### 2. Pipeline Tests

Test the complete frame processing pipeline:

```python
@pytest.mark.performance
def test_full_pipeline():
    """Complete pipeline should be < 15ms."""
    # Test resize + encode + overhead
    # Measure P95 and P99 latencies
```

#### 3. Sustained Throughput Tests

Test continuous processing over time:

```python
@pytest.mark.performance
def test_sustained_30fps():
    """Can we sustain 30fps for 10 seconds?"""
    # Process 300 frames
    # Check drop rate < 5%
    # Verify no memory leaks
```

## Understanding Performance Metrics

When you run performance tests, you'll see output like:

```
📊 Frame Resize Performance:
   Mean:   2.34 ms
   P95:    3.12 ms
   Max:    4.89 ms
```

**What these mean:**
- **Mean**: Average time across all runs
- **P95**: 95% of operations complete in this time (good for SLAs)
- **P99**: 99% of operations complete in this time
- **Max**: Worst-case observed

**Why P95/P99 matter:**
- Mean can hide outliers
- P95 tells you what most users experience
- P99 tells you worst-case for almost all users

### Interpreting Results

✅ **Good**: P95 is well under budget, mean is close to P95
```
Mean: 2.1 ms, P95: 2.4 ms  (consistent, predictable)
```

⚠️ **Warning**: P95 exceeds budget or high variance
```
Mean: 2.1 ms, P95: 8.3 ms  (unpredictable, occasional spikes)
```

❌ **Bad**: Mean exceeds budget
```
Mean: 15.2 ms, P95: 22.1 ms  (too slow, will drop frames)
```

## Performance Testing Best Practices

### 1. Test on Representative Hardware

Performance can vary dramatically across hardware:
- **Development**: May be on powerful workstation
- **Production**: May be on embedded device or cloud VM

**Recommendation**: Run performance tests on target hardware or use CI with representative specs.

### 2. Use Warmup Runs

Always include warmup iterations:

```python
# Warmup (JIT, cache warming, etc.)
for _ in range(2):
    processor.process_frame(frame)

# Actual measurement
for _ in range(10):
    start = time.perf_counter()
    processor.process_frame(frame)
    duration = time.perf_counter() - start
```

### 3. Multiple Iterations

Run multiple iterations to get statistical significance:
- **Minimum**: 10 iterations
- **Recommended**: 50-100 iterations
- **Long-running tests**: 1000+ iterations

### 4. Use Realistic Data

Test with realistic inputs:
```python
# Bad: zeros (optimized by CPU/GPU)
frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

# Good: random data (more realistic)
frame = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)
```

### 5. Soft vs. Hard Assertions

Use **soft warnings** for budgets and **hard assertions** for absolute limits:

```python
# Soft: warn if exceeds budget
if stats['mean'] > BUDGET:
    print(f"⚠️  Warning: Exceeds budget")

# Hard: must be under frame time
assert stats['p95'] < FRAME_TIME, "Too slow for real-time"
```

This approach:
- Gives early warning of performance regressions
- Doesn't fail CI on slower hardware
- Has hard limit to prevent shipping broken code

## Profiling for Optimization

When performance tests show issues, use profiling to find bottlenecks.

### Option 1: Use the Profiling Script

```bash
# Profile video processor
./scripts/profile_code.sh video_processor --visualize

# Profile VLM service
./scripts/profile_code.sh vlm_service
```

### Option 2: Manual Profiling

```bash
# Generate profile
python -m cProfile -o profile.stats \
    -m pytest tests/unit/test_video_processor.py::TestVideoProcessorPerformance

# Analyze interactively
python -m pstats profile.stats
>>> sort cumulative
>>> stats 20

# Visualize (requires snakeviz)
pip install snakeviz
snakeviz profile.stats
```

### Option 3: Line-by-Line Profiling

For very detailed analysis:

```bash
# Install line_profiler
pip install line_profiler

# Add @profile decorator to function
# Run kernprof
kernprof -l -v your_script.py
```

## Continuous Performance Monitoring

### In CI/CD

The GitHub Actions workflow includes performance tests:
- Runs on every PR
- Results are informational (don't block merge)
- Tracks trends over time

### Performance Regression Detection

To detect regressions:

1. **Save baseline**:
   ```bash
   ./scripts/run_performance_tests.sh --save-baseline
   ```

2. **Compare against baseline**:
   ```bash
   ./scripts/run_performance_tests.sh --compare
   ```

3. **Track in version control**:
   ```bash
   git add .pytest_performance_baseline.json
   git commit -m "Update performance baseline"
   ```

## Optimizing for Real-Time Performance

### General Tips

1. **Use NumPy efficiently**: Avoid loops, use vectorized operations
2. **Resize smartly**: Use `cv2.INTER_LINEAR` (fast) instead of `cv2.INTER_CUBIC` (slow)
3. **JPEG quality**: Lower quality = faster encoding (use 85 instead of 95)
4. **Async everything**: Don't block the frame processing loop
5. **Queue VLM requests**: Process frames continuously, queue VLM inference

### Video Processing Optimizations

```python
# Good: Fast resize with appropriate interpolation
resized = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)

# Good: Reasonable JPEG quality
_, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])

# Good: Process on smaller resolution
resized = cv2.resize(frame, (640, 480))  # Resize first
result = heavy_processing(resized)       # Then process
```

### Async Architecture

For VLM inference (slow), use async queuing:

```python
class VideoProcessor:
    def __init__(self):
        self.vlm_queue = asyncio.Queue()

    async def process_frame(self, frame):
        # Fast path: resize + encode (< 15ms)
        resized = self.resize_frame(frame)
        encoded = self.encode_frame(resized)

        # Queue VLM inference (don't wait)
        if self.should_query_vlm():
            await self.vlm_queue.put((frame, query))

        return encoded

    async def vlm_worker(self):
        # Separate worker processes VLM queue
        while True:
            frame, query = await self.vlm_queue.get()
            response = await self.vlm.query(query, frame)
            self.broadcast_response(response)
```

## FAQ

### Q: My performance tests are failing on CI but passing locally

**A**: Different hardware. Consider:
- Adjusting constraints for CI hardware
- Making performance tests informational (warnings, not failures)
- Testing on representative hardware

### Q: Should all functions have performance tests?

**A**: No, focus on:
- Functions in the critical path (frame processing)
- Functions with timing constraints
- Async operations that might block

Skip for:
- One-time initialization
- Infrequent operations (config loading)
- Non-critical background tasks

### Q: How do I set appropriate time budgets?

**A**:
1. Start with no assertions, just measure
2. Run on target hardware, collect statistics
3. Set P95 target at 80% of acceptable limit
4. Add soft warnings at 50% of limit

### Q: Performance tests are flaky

**A**: Common causes:
- Not enough warmup iterations
- Not enough measurement iterations
- Background processes on test machine
- CPU throttling / power saving mode

Solutions:
- Increase iterations
- Use P95/P99 instead of max
- Run on dedicated test hardware
- Disable power saving: `cpupower frequency-set -g performance`

## Summary

**Key Takeaways:**
1. ✅ Performance tests are essential for real-time video processing
2. ✅ Use statistical metrics (mean, P95, P99), not just single runs
3. ✅ Test individual functions AND the complete pipeline
4. ✅ Use soft warnings for budgets, hard assertions for limits
5. ✅ Profile to find bottlenecks, optimize hot paths
6. ✅ Test on representative hardware, track regressions

**Next Steps:**
- Run `./scripts/run_performance_tests.sh` to see current performance
- Identify any warnings or failures
- Use `./scripts/profile_code.sh` to find bottlenecks
- Optimize hot paths
- Re-test and save baseline

