"""Performance tests for GPU monitoring - real implementation."""

import pytest
import time
from tests.utils.performance import PerformanceMetrics


@pytest.mark.performance
class TestGPUMonitorPerformance:
    """Test GPU monitor performance."""

    def test_get_stats_performance(self):
        """Test that get_stats() is fast enough for real-time updates."""
        from live_vlm_webui.gpu_monitor import create_monitor

        monitor = create_monitor()
        metrics = PerformanceMetrics()

        print("\n⚡ Testing GPU stats retrieval performance")
        print("   Target: < 5ms (NVML is fast, sub-millisecond)")
        print("")

        # Run 100 iterations to get good statistics
        num_iterations = 100

        for i in range(num_iterations):
            start = time.perf_counter()
            monitor.get_stats()
            duration_ms = (time.perf_counter() - start) * 1000
            metrics.record("get_stats", duration_ms)

        result = metrics.get_stats("get_stats")

        print(f"📊 Results ({num_iterations} iterations):")
        print(f"   Mean:   {result['mean']:.3f} ms")
        print(f"   Median: {result['median']:.3f} ms")
        print(f"   P95:    {result['p95']:.3f} ms")
        print(f"   P99:    {result['p99']:.3f} ms")
        print(f"   Max:    {result['max']:.3f} ms")
        print("")

        # NVML calls are fast (sub-millisecond)
        # This test ensures performance doesn't regress
        if result["p95"] < 5.0:
            print(f"   ✅ Performance excellent (P95: {result['p95']:.3f} ms)")
        else:
            print(f"   ⚠️  Warning: P95 latency high ({result['p95']:.3f} ms)")

        # GPU stats should be fast enough to not impact real-time performance
        # Allow 5ms P95 (reasonable for monitoring with occasional spikes)
        assert (
            result["p95"] < 5.0
        ), f"GPU stats retrieval too slow: P95={result['p95']:.2f}ms (limit: 5ms)"

    def test_monitor_creation_time(self):
        """Test that monitor creation is reasonably fast."""
        from live_vlm_webui.gpu_monitor import create_monitor

        print("\n🏗️  Testing monitor creation time")

        start = time.perf_counter()
        create_monitor()
        creation_time_ms = (time.perf_counter() - start) * 1000

        print(f"   Creation time: {creation_time_ms:.2f} ms")
        print("")

        # Creation should be fast (< 1 second)
        if creation_time_ms < 100:
            print(f"   ✅ Fast startup ({creation_time_ms:.2f} ms)")
        elif creation_time_ms < 1000:
            print(f"   ⚠️  Slow startup ({creation_time_ms:.2f} ms)")
        else:
            print(f"   ❌ Very slow startup ({creation_time_ms:.2f} ms)")

        assert creation_time_ms < 1000, f"Monitor creation too slow: {creation_time_ms:.2f}ms"

    @pytest.mark.slow
    def test_sustained_monitoring(self):
        """Test sustained GPU monitoring over time."""
        from live_vlm_webui.gpu_monitor import create_monitor

        monitor = create_monitor()
        metrics = PerformanceMetrics()

        duration_seconds = 5
        poll_interval = 0.1  # 100ms between polls (10Hz)

        print("\n⏱️  Sustained monitoring test")
        print(f"   Duration: {duration_seconds}s")
        print(f"   Poll rate: {1/poll_interval:.0f} Hz")
        print("")

        start_time = time.perf_counter()
        poll_count = 0

        while (time.perf_counter() - start_time) < duration_seconds:
            poll_start = time.perf_counter()
            monitor.get_stats()
            poll_duration = (time.perf_counter() - poll_start) * 1000

            metrics.record("poll", poll_duration)
            poll_count += 1

            # Wait for next poll
            time.sleep(poll_interval)

        total_time = time.perf_counter() - start_time
        actual_rate = poll_count / total_time

        result = metrics.get_stats("poll")

        print("📊 Results:")
        print(f"   Total polls: {poll_count}")
        print(f"   Actual rate: {actual_rate:.1f} Hz")
        print(f"   Mean poll time: {result['mean']:.3f} ms")
        print(f"   P95 poll time:  {result['p95']:.3f} ms")
        print("")

        if result["p95"] < 5.0:
            print("   ✅ Sustained performance excellent")
        else:
            print("   ⚠️  Warning: Performance degraded over time")

        # Ensure performance stays fast over sustained operation
        assert (
            result["p95"] < 10.0
        ), f"Sustained performance degraded: P95={result['p95']:.2f}ms (limit: 10ms)"
