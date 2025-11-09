# Multi-Hardware Performance Testing Strategy

## Your Hardware Range

- **PCs** - Vary widely, development machines
- **Jetson Thor / DGX Spark** - Most powerful ARM machines
- **Jetson AGX Orin Developer Kit** - High performance
- **Jetson Orin Nano Developer Kit** - Weakest target (6 CPU cores) ⚠️
- **Mac** - Development machines

## Strategy: Local Due Diligence + Target Baselines

### 1. Local Development (Not Committed)

**Each developer maintains their own baseline:**

```bash
# On your dev machine (PC, Mac, whatever)
$ ./scripts/run_performance_tests.sh --save-baseline
💾 Baseline saved to: .performance_baseline.json

# After making changes
$ ./scripts/run_performance_tests.sh
✅ Performance stable (within 20% of baseline)
# or
⚠️  PERFORMANCE REGRESSION DETECTED!
```

**This file is `.gitignore`d** - each developer tracks their own performance.

### 2. Target Hardware Baselines (Committed)

**Optionally commit baselines for deployment targets:**

```bash
# On Jetson Orin Nano (weakest target)
$ ./scripts/run_performance_tests.sh --save-baseline \
    --baseline-file .baseline-jetson-orin-nano.json

# On Jetson AGX Orin
$ ./scripts/run_performance_tests.sh --save-baseline \
    --baseline-file .baseline-jetson-agx-orin.json

# Commit these
$ git add .baseline-*.json
$ git commit -m "Add target hardware baselines"
```

**These ARE committed** - represents target deployment performance.

### 3. Hard Limits Based on Weakest Hardware

**The key principle:** If it works on **Orin Nano (weakest)**, it works everywhere!

```python
# In test code - set hard limits based on Orin Nano
@pytest.mark.performance
def test_frame_processing_regression(regression_tracker):
    # ... measure performance ...

    # SOFT: Check regression vs local baseline
    # Each developer compares to their own machine
    regression = regression_tracker.check_regression(
        "frame_processing",
        stats['mean'],
        stats['p95'],
        threshold_percent=20.0
    )

    # HARD: Must work on Orin Nano (weakest)
    # This limit is ABSOLUTE across all hardware
    # Based on actual Orin Nano measurements
    assert stats['p95'] < 30.0, \
        "CRITICAL: Too slow for Jetson Orin Nano!"

    # HARD: Must support 30fps
    assert stats['p95'] < 33.33, \
        "CRITICAL: Cannot maintain 30fps!"
```

## File Structure

```
.performance_baseline.json          # Local (gitignored)
.baseline-jetson-orin-nano.json     # Committed (CI target)
.baseline-jetson-agx-orin.json      # Committed (optional)
.baseline-jetson-thor.json          # Committed (optional)
```

**.gitignore:**
```bash
# Local baselines (each developer has their own)
.performance_baseline.json

# Target hardware baselines are COMMITTED
# .baseline-jetson-*.json
```

## Workflow Examples

### Developer on PC (No Jetson Access)

```bash
# 1. Establish your local baseline
$ ./scripts/run_performance_tests.sh --save-baseline
Frame processing: 5.2 ms (baseline saved)

# 2. Make changes
$ vim src/live_vlm_webui/video_processor.py

# 3. Check YOUR regression
$ ./scripts/run_performance_tests.sh
Frame processing: 6.8 ms (+30.8%)
⚠️  REGRESSION! (on MY machine)

# 4. Fix it
# ... optimize code ...

# 5. Re-test
$ ./scripts/run_performance_tests.sh
Frame processing: 4.9 ms (-5.8%)
✅ Stable

# 6. Optional: Check against Orin Nano baseline
$ ./scripts/run_performance_tests.sh \
    --baseline-file .baseline-jetson-orin-nano.json
✅ Still within Orin Nano limits
```

**You don't need the actual hardware!** The hard limits and committed baselines tell you if it will work.

### Developer with Jetson Orin Nano

```bash
# 1. Establish local baseline
$ ./scripts/run_performance_tests.sh --save-baseline
Frame processing: 24.3 ms (baseline saved on Orin Nano)

# 2. Update target baseline if needed
$ ./scripts/run_performance_tests.sh --save-baseline \
    --baseline-file .baseline-jetson-orin-nano.json
$ git add .baseline-jetson-orin-nano.json
$ git commit -m "Update Orin Nano baseline"

# 3. This baseline helps other developers
# They can test against it without the hardware!
```

### CI/CD

```yaml
# .github/workflows/performance.yml
- name: Performance Tests
  run: |
    # Test against Orin Nano baseline (weakest target)
    ./scripts/run_performance_tests.sh \
      --baseline-file .baseline-jetson-orin-nano.json || true

    # Don't fail CI (hardware is different)
    # But output shows if we regressed vs. target
```

## Setting Hard Limits

**Process:**

1. **Test on Orin Nano** (or weakest hardware you have)
   ```bash
   $ ./scripts/run_performance_tests.sh
   Frame processing: Mean=22.1ms, P95=24.5ms, P99=26.8ms
   ```

2. **Add safety margin** (20% headroom recommended)
   ```
   P95 = 24.5 ms
   Limit = 24.5 * 1.2 = 29.4 ms ≈ 30 ms
   ```

3. **Set hard limit in test code**
   ```python
   assert stats['p95'] < 30.0, \
       "Too slow for Jetson Orin Nano (measured: 24.5ms, limit: 30ms)"
   ```

4. **Verify on other hardware**
   ```bash
   # AGX Orin: 8.2 ms ✅ (well under 30ms)
   # Thor:     3.1 ms ✅ (well under 30ms)
   # PC:       5.7 ms ✅ (well under 30ms)
   ```

## Benefits of This Approach

✅ **No hardware required** - Developers can work on any machine
✅ **Local due diligence** - Each developer tracks their own regressions
✅ **Target hardware assurance** - Hard limits based on weakest device
✅ **No brittle CI** - Warnings only, doesn't fail on hardware mismatch
✅ **Clear constraints** - "Works on Orin Nano" = works everywhere

## Summary

**Three layers of protection:**

1. **Local regression detection**
   - Each developer: `./scripts/run_performance_tests.sh`
   - Compares to their own baseline
   - Catches regressions early

2. **Target baseline comparison**
   - Test against: `.baseline-jetson-orin-nano.json`
   - See if you're within target limits
   - No actual hardware needed!

3. **Hard limits (Orin Nano-based)**
   - `assert stats['p95'] < 30.0`
   - Based on actual Orin Nano measurements
   - Gates shipping code

**Developer responsibility:**
- Maintain your local baseline
- Check for regressions on your hardware
- Verify against target baselines (optional but recommended)
- Hard limits ensure it works on target hardware

**No need to push individual baselines** - only commit target hardware baselines that everyone tests against!

