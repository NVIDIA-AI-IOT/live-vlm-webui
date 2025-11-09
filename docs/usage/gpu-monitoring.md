# GPU Monitoring and Utilization

## Understanding GPU Utilization Fluctuations

If you notice that GPU utilization (shown in the web UI) **fluctuates wildly** between 0% and 100%, including frequent drops to 0% even during active inference, especially with smaller VLM and with fast GPU - **this is completely normal behavior** for LLM/VLM workloads.

### What You'll See

```
GPU: 100% → 0% → 100% → 0% → 100% → 30% → 0% → 100%
```

This pattern is expected and (most likely) **NOT a bug**.

---

## Why Does This Happen?

### 1. **Bursty Workload Nature**

LLM/VLM inference is not a continuous GPU task. It's a series of **short GPU bursts** separated by CPU work:

```
Timeline during 868ms inference:

Time 0ms:     Image encoding      → GPU: 100% (50-100ms burst)
Time 100ms:   CPU prepares data   → GPU: 0%
Time 120ms:   Attention layers    → GPU: 100% (20ms burst)
Time 140ms:   CPU scheduling      → GPU: 0%
Time 145ms:   Generate token 1    → GPU: 100% (5ms burst)
Time 150ms:   CPU processes token → GPU: 0%
Time 155ms:   Generate token 2    → GPU: 100% (5ms burst)
Time 160ms:   CPU processes token → GPU: 0%
...
Time 868ms:   Done!
```

### 2. **Token-by-Token Generation**

Vision Language Models generate text **one token at a time**:
- GPU computes next token → **GPU: 100%** (5-10ms)
- CPU processes result → **GPU: 0%** (idle)
- Repeat for each token...

### 3. **Memory Transfer Overhead**

Data movement between CPU and GPU causes idle periods:
- GPU finishes computation
- Results transferred to CPU memory
- **GPU idles** during transfer
- Next batch prepared and transferred
- GPU resumes work

### 4. **Framework Overhead**

PyTorch/CUDA operations have overhead:
- Kernel launch latency
- Synchronization points
- Memory allocations
- **GPU sits idle** during these operations

---

## NVML Reports Instantaneous Values

The GPU monitoring uses NVIDIA's **NVML (NVIDIA Management Library)**, which reports **instantaneous** GPU utilization at the exact moment you query it.

Our monitor polls every **250ms**:
- If it polls during a GPU burst → shows **100%**
- If it polls during CPU work → shows **0%**
- If it polls mid-operation → shows **30-80%**

### Wall-Clock Latency ≠ GPU-Busy Time

When VLM inference shows **"latency: 868ms"**:
- **Wall-clock time**: 868ms (what you experience)
- **GPU-busy time**: ~200-400ms (actual GPU work)
- **Idle time**: ~400-600ms (CPU work, transfers, overhead)

The GPU is only **30-50% busy** during that 868ms window!

---

## Frame Processing Gaps

Between video frames, the GPU is completely idle:

```
Frame 1: Process (100ms) → Inference (868ms) → Done
         ↓
         GPU: 0% (waiting for next frame)
         ↓
Frame 2: Process (100ms) → Inference (868ms) → Done
```

If frames arrive every 3 frames (~100ms apart) and inference takes 868ms:
- Some frames get queued/skipped
- GPU cycles between **bursts of work** and **idle periods**

---

## Faster Models = MORE Idle Time

Counter-intuitively, **faster VLMs show MORE 0% readings**:

### Slow Model (llama3.2-vision, ~1500ms inference):
- Inference: 1500ms
- Frame interval: ~100ms
- GPU working longer per frame

### Fast Model (gemma3:4b, ~500ms inference):
- Inference: 500ms
- Frame interval: ~100ms
- **GPU done quickly, then idles for 400ms+ until next frame**
- Result: **MORE 0% readings** because GPU finishes faster!

---

## How to Interpret GPU Stats

### ✅ **Normal Patterns:**
- Wild fluctuations (0% to 100%)
- Brief periods of 0% during active inference
- Higher utilization during image encoding
- Lower utilization during text generation
- Complete idle between frames

### ⚠️ **Potential Issues:**
- Sustained 0% for multiple seconds when inference SHOULD be happening
- No fluctuation at all (stuck at one value)
- Error messages in logs about NVML

---

## Technical Details

### What NVML Actually Reports

`nvmlDeviceGetUtilizationRates()` returns the GPU utilization percentage **at the moment of the call**, averaged over a very short window (typically 1-20ms, hardware-dependent).

It does **NOT** return:
- Average utilization since last call
- Peak utilization
- Accumulated GPU time

### Why Not Use Average Utilization?

We could smooth the display by averaging multiple samples, but that would:
- Hide the real behavior of your workload
- Make it harder to debug performance issues
- Give a false sense of GPU usage

The **raw, instantaneous values** are the most accurate representation of what's actually happening.

---

## Conclusion

**Wild GPU utilization fluctuations are expected and normal** for LLM/VLM workloads. This is the nature of:
- Token-by-token generation
- Bursty GPU operations
- CPU-GPU coordination
- Framework overhead

The monitoring system is working correctly - it's showing you the **real, instantaneous** GPU state!

---

## Related Topics

- [Advanced Configuration](./advanced-configuration.md) - Adjust inference settings
- [Performance Tuning](../development/performance-testing.md) - Optimize for your hardware
- [Troubleshooting](../troubleshooting.md) - Debug actual GPU issues

