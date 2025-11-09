# Performance Regression Testing Guide

## Overview

Instead of setting fixed time budgets for each function, we use **performance regression testing** to automatically detect when functions get significantly slower than their baseline.

## Why Regression Testing?

### Problems with Fixed Budgets
- ❌ Hard to determine appropriate budgets upfront
- ❌ Different hardware has different performance
- ❌ Brittle tests that fail on slower machines
- ❌ Doesn't catch gradual slowdowns

### Benefits of Regression Testing
- ✅ Automatically detects performance degradation
- ✅ Works across different hardware
- ✅ Tracks actual performance over time
- ✅ Catches slowdowns early
- ✅ Can track improvements too!

## How It Works

### 1. Establish Baseline (First Run)

```bash
./scripts/run_performance_tests.sh --save-baseline
```

This will:
1. Run all performance tests
2. Measure mean, median, P95, P99 for each function
3. Save results to `.performance_baseline.json`

**Example output:**
```
📊 Frame Resize Performance:
   Mean:   2.34 ms
   Median: 2.21 ms
   P95:    3.12 ms
   P99:    3.89 ms

No baseline found. Run with --save-baseline to establish baseline.

💾 Baseline saved!
```

### 2. Run Tests (Compare Against Baseline)

```bash
./scripts/run_performance_tests.sh
```

This will:
1. Run all performance tests
2. Compare results against baseline
3. Flag any regressions > 20%

**Example output (no regression):**
```
📊 Frame Resize Performance:
   Mean:   2.28 ms
   P95:    3.05 ms

✅ Performance stable (within 20% of baseline)
   Mean: 2.34 → 2.28 ms (-2.6%)
   P95:  3.12 → 3.05 ms (-2.2%)
```

**Example output (regression detected):**
```
📊 Frame Resize Performance:
   Mean:   3.21 ms
   P95:    4.15 ms

⚠️  PERFORMANCE REGRESSION DETECTED!
   Function: video_processor.resize_frame
   Mean: 2.34 → 3.21 ms (+37.2%)
   P95:  3.12 → 4.15 ms (+33.0%)
   Threshold: 20%

⚠️  Regression detected but not failing test (set FAIL_ON_REGRESSION=1 to fail)
```

### 3. Two-Tier Protection

Even with regression testing, we keep **hard limits** for critical constraints:

```python
# Soft: Warn if >20% slower than baseline
if regression_detected:
    print("⚠️  Warning: Performance regression")

# Hard: Must be under 33ms for 30fps (critical!)
assert stats['p95'] < 33.33, "CRITICAL: Too slow for real-time!"
```

This gives you:
- **Early warning** via regression detection
- **Hard safety net** for critical timing constraints

## Workflow

### Initial Setup

```bash
# 1. Run performance tests and establish baseline
./scripts/run_performance_tests.sh --save-baseline

# 2. Commit the baseline
git add .performance_baseline.json
git commit -m "Add performance baseline"
```

### During Development

```bash
# After making changes, run performance tests
./scripts/run_performance_tests.sh

# Three possible outcomes:
# 1. ✅ Stable (within 20% of baseline) - good!
# 2. 🎉 Improvement (>20% faster) - great! Update baseline
# 3. ⚠️  Regression (>20% slower) - investigate!
```

### If Regression Detected

```bash
# 1. Profile to find bottleneck
./scripts/profile_code.sh video_processor --visualize

# 2. Optimize the slow code

# 3. Re-run tests
./scripts/run_performance_tests.sh

# 4. If fixed, update baseline
./scripts/run_performance_tests.sh --save-baseline

# 5. Commit new baseline
git add .performance_baseline.json
git commit -m "Optimize video processing, update baseline"
```

### If Improvement Detected

```bash
# Great! Update the baseline
./scripts/run_performance_tests.sh --save-baseline

git add .performance_baseline.json
git commit -m "Performance improvement: faster frame processing"
```

## Commands

### Basic Usage

```bash
# Run and compare with baseline (default)
./scripts/run_performance_tests.sh

# Save/update baseline
./scripts/run_performance_tests.sh --save-baseline

# Show current baseline
./scripts/run_performance_tests.sh --show-baseline

# Fail tests on regression (for CI)
./scripts/run_performance_tests.sh --fail-on-regression
```

### Advanced Options

```bash
# Use custom baseline file
./scripts/run_performance_tests.sh --baseline-file .baseline-prod.json

# Environment variables (for direct pytest)
export SAVE_PERFORMANCE_BASELINE=1
pytest tests/unit/test_video_processor.py::TestVideoProcessorPerformance -v -s

export FAIL_ON_REGRESSION=1
pytest -m performance
```

## Configuration

### Regression Threshold

Default: **20%** (configurable in test code)

```python
regression_result = regression_tracker.check_regression(
    "function_name",
    current_mean,
    current_p95,
    threshold_percent=20.0  # Adjust this
)
```

**Choosing a threshold:**
- **10%**: Very sensitive, catches small regressions
- **20%**: Balanced (recommended)
- **30%**: Less sensitive, fewer false positives

### Hard Limits for Target Hardware

Keep hard limits for **critical real-time constraints**, but base them on your **weakest target hardware**!

**Important:** Hard limits should be set based on **Jetson Orin Nano** (your weakest device):
- If it works on Orin Nano (6 cores), it works everywhere
- Prevents shipping code that's too slow for actual deployments

```python
# Hard limits based on WEAKEST hardware (Orin Nano)
# These limits are ABSOLUTE - must pass on all hardware

# Example: Frame processing MUST be under 33ms for 30fps
# Test this on Orin Nano first!
assert stats['p95'] < PerformanceConstraints.FRAME_TIME_30FPS, \
    "CRITICAL: Too slow for real-time on Orin Nano!"
```

**Workflow for setting hard limits:**

```bash
# 1. Test on Jetson Orin Nano (weakest hardware)
$ ./scripts/run_performance_tests.sh
Frame processing: P95 = 24.5 ms ✅

# 2. Set hard limit with headroom (20% margin)
# P95 = 24.5 ms, so set limit at 30ms
assert stats['p95'] < 30.0, "Too slow for Orin Nano"

# 3. This ensures it works on all other hardware too
# AGX Orin: P95 = 8.2 ms ✅
# Thor:     P95 = 3.1 ms ✅
# PC:       P95 = 5.7 ms ✅
```

## Baseline File Format

The `.performance_baseline.json` file stores baselines:

```json
{
  "video_processor.resize_frame": {
    "function_name": "video_processor.resize_frame",
    "mean_ms": 2.34,
    "median_ms": 2.21,
    "p95_ms": 3.12,
    "p99_ms": 3.89,
    "sample_size": 20,
    "timestamp": "2025-11-08T10:30:15",
    "hardware_info": "x86_64 | 8 cores | 16GB RAM"
  },
  "video_processor.encode_frame": {
    ...
  }
}
```

## CI/CD Integration

### GitHub Actions

```yaml
# .github/workflows/performance.yml
- name: Run performance tests
  run: |
    # Compare against baseline (warn only)
    ./scripts/run_performance_tests.sh

    # Or fail on regression
    # ./scripts/run_performance_tests.sh --fail-on-regression
```

### Pre-commit Hook

```bash
# .git/hooks/pre-commit
#!/bin/bash
# Run quick performance check
./scripts/run_performance_tests.sh || true
```

## Best Practices

### 1. Commit Baselines

Always commit the baseline file to version control:

```bash
git add .performance_baseline.json
git commit -m "Update performance baseline"
```

This allows:
- Tracking performance over time
- Comparing across branches
- Team-wide consistency

### 2. Update Baseline After Intentional Changes

If you make changes that intentionally affect performance:

```bash
# Example: Changed JPEG quality for better compression
./scripts/run_performance_tests.sh --save-baseline
git add .performance_baseline.json
git commit -m "Adjust JPEG quality, update baseline"
```

### 3. Multiple Baselines for Different Hardware

**Your hardware diversity:**
- PCs (vary a lot)
- Jetson Thor / DGX Spark (most powerful ARM)
- Jetson AGX Orin Developer Kit (powerful)
- Jetson Orin Nano Developer Kit (weak, 6 CPU cores)
- Mac

**Recommended Approach: Local Due Diligence**

Each developer maintains their **own local baseline** and is responsible for checking regressions on their hardware:

```bash
# Developer on PC
./scripts/run_performance_tests.sh --save-baseline
# Creates .performance_baseline.json (NOT committed)

# Developer on Jetson Orin Nano
./scripts/run_performance_tests.sh --save-baseline
# Creates their own .performance_baseline.json (NOT committed)

# Each developer checks their own hardware
./scripts/run_performance_tests.sh
# Compares against THEIR baseline
```

**Why this works:**
- ✅ Each developer tracks regressions on their machine
- ✅ No brittle CI failures from hardware differences
- ✅ Developers are responsible for due diligence
- ✅ Works even if you don't have all hardware

**Add to `.gitignore`:**
```bash
# Performance baselines (each developer has their own)
.performance_baseline.json
```

**Optional: Commit baselines for target hardware**

If you want CI to test against specific deployment targets:

```bash
# On Jetson Orin Nano (weakest target)
./scripts/run_performance_tests.sh --save-baseline \
    --baseline-file .baseline-jetson-orin-nano.json

# Commit ONLY target hardware baselines
git add .baseline-jetson-orin-nano.json
git add .baseline-jetson-agx-orin.json
git commit -m "Add baselines for target hardware"

# In CI: Test against target hardware baseline
./scripts/run_performance_tests.sh \
    --baseline-file .baseline-jetson-orin-nano.json
```

**Hybrid Approach (Recommended):**

```bash
# 1. Local development (not committed)
.performance_baseline.json          # Developer's local baseline

# 2. Target hardware baselines (committed)
.baseline-jetson-orin-nano.json     # Weakest target
.baseline-jetson-agx-orin.json      # Mid-range target
.baseline-jetson-thor.json          # High-end target

# 3. CI uses target hardware
# Only warns, doesn't fail on different hardware
```

**Example workflow:**

```bash
# Developer on PC (local due diligence)
$ ./scripts/run_performance_tests.sh --save-baseline
$ # ... make changes ...
$ ./scripts/run_performance_tests.sh
⚠️  Regression detected on my PC!
$ # Fix it

# Optional: Test against Jetson Orin Nano baseline
$ ./scripts/run_performance_tests.sh \
    --baseline-file .baseline-jetson-orin-nano.json
✅ Still within limits for Orin Nano

# CI: Tests against committed target baseline
# (informational only, doesn't fail)
```

### 4. Regular Performance Monitoring

```bash
# Weekly/monthly: Update baseline and track trends
./scripts/run_performance_tests.sh --save-baseline

# Compare with older baselines
git show HEAD~10:.performance_baseline.json > .baseline-old.json
./scripts/run_performance_tests.sh --baseline-file .baseline-old.json
```

## Troubleshooting

### "No baseline found"

**Solution:** Run with `--save-baseline` to establish baseline:

```bash
./scripts/run_performance_tests.sh --save-baseline
```

### Regression on CI but not locally

**Cause:** Different hardware performance

**Solutions:**
1. Don't fail on regression in CI (warn only)
2. Create separate baseline for CI hardware
3. Increase regression threshold for CI

```bash
# In CI, use warning mode
./scripts/run_performance_tests.sh  # warnings only

# Or create CI-specific baseline
./scripts/run_performance_tests.sh --baseline-file .baseline-ci.json
```

### False positives (flaky performance)

**Cause:** Not enough iterations, background processes

**Solutions:**
1. Increase iterations in tests
2. Use P95/P99 instead of mean
3. Run on dedicated hardware
4. Increase threshold (20% → 30%)

### Baseline file conflicts in git

**Solution:** Choose one baseline and commit:

```bash
# Keep the newer baseline
git checkout --theirs .performance_baseline.json

# Or regenerate
./scripts/run_performance_tests.sh --save-baseline
```

## Example: Full Workflow

```bash
# Day 1: Establish baseline
./scripts/run_performance_tests.sh --save-baseline
git add .performance_baseline.json
git commit -m "Establish performance baseline"

# Day 2: Make changes
vim src/live_vlm_webui/video_processor.py

# Run quick tests
./scripts/test_quick.sh

# Run performance tests
./scripts/run_performance_tests.sh
# Output: ⚠️  PERFORMANCE REGRESSION DETECTED!
#         Mean: 2.34 → 3.21 ms (+37.2%)

# Investigate
./scripts/profile_code.sh video_processor --visualize
# Found: Inefficient loop in resize function

# Optimize
vim src/live_vlm_webui/video_processor.py

# Re-test
./scripts/run_performance_tests.sh
# Output: 🎉 PERFORMANCE IMPROVEMENT!
#         Mean: 2.34 → 1.89 ms (-19.2%)

# Update baseline
./scripts/run_performance_tests.sh --save-baseline
git add .performance_baseline.json src/live_vlm_webui/video_processor.py
git commit -m "Optimize video processing, 20% faster"
```

## Summary

**Key Points:**
- ✅ Regression testing > fixed budgets
- ✅ Automatically detects when functions get slower
- ✅ Works across different hardware
- ✅ Still keeps hard limits for critical constraints
- ✅ Track performance over time
- ✅ Easy to integrate into workflow

**Commands to Remember:**
```bash
./scripts/run_performance_tests.sh --save-baseline  # Establish/update baseline
./scripts/run_performance_tests.sh                  # Compare with baseline
./scripts/run_performance_tests.sh --show-baseline  # Show current baseline
```

**Workflow:**
1. Establish baseline
2. Make changes
3. Run tests (compare)
4. If regression: profile & optimize
5. Update baseline
6. Commit!

