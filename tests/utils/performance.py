"""Performance testing utilities."""

import time
import functools
import statistics
from typing import Callable, Dict, List
from contextlib import contextmanager


class PerformanceConstraints:
    """Performance constraints for real-time video processing."""

    # Frame time constraints (in milliseconds)
    FRAME_TIME_30FPS = 33.33  # 30 fps
    FRAME_TIME_60FPS = 16.67  # 60 fps
    FRAME_TIME_24FPS = 41.67  # 24 fps (cinema)

    # Component budget guidelines (approximate, in milliseconds)
    # These are soft guidelines, not hard requirements
    VIDEO_DECODE_BUDGET = 5.0  # Video decoding
    FRAME_PROCESSING_BUDGET = 3.0  # Frame preprocessing (resize, encode)
    VLM_INFERENCE_BUDGET = 100.0  # VLM inference (can be async/queued)
    NETWORK_BUDGET = 10.0  # Network I/O

    # Total real-time budget for frame processing pipeline
    # (excluding VLM inference which can be queued)
    REALTIME_PIPELINE_BUDGET = 15.0  # milliseconds


class PerformanceMetrics:
    """Collect and analyze performance metrics."""

    def __init__(self):
        self.timings: Dict[str, List[float]] = {}

    def record(self, name: str, duration_ms: float):
        """Record a timing measurement."""
        if name not in self.timings:
            self.timings[name] = []
        self.timings[name].append(duration_ms)

    def get_stats(self, name: str) -> Dict[str, float]:
        """Get statistical summary of timings."""
        if name not in self.timings or not self.timings[name]:
            return {}

        timings = self.timings[name]
        return {
            "count": len(timings),
            "mean": statistics.mean(timings),
            "median": statistics.median(timings),
            "min": min(timings),
            "max": max(timings),
            "stdev": statistics.stdev(timings) if len(timings) > 1 else 0.0,
            "p95": statistics.quantiles(timings, n=20)[18] if len(timings) > 1 else timings[0],
            "p99": statistics.quantiles(timings, n=100)[98] if len(timings) > 1 else timings[0],
        }

    def summary(self) -> str:
        """Generate a summary report."""
        lines = ["Performance Metrics Summary:", "=" * 60]

        for name in sorted(self.timings.keys()):
            stats = self.get_stats(name)
            lines.append(f"\n{name}:")
            lines.append(f"  Mean:   {stats['mean']:.2f} ms")
            lines.append(f"  Median: {stats['median']:.2f} ms")
            lines.append(f"  Min:    {stats['min']:.2f} ms")
            lines.append(f"  Max:    {stats['max']:.2f} ms")
            lines.append(f"  P95:    {stats['p95']:.2f} ms")
            lines.append(f"  P99:    {stats['p99']:.2f} ms")
            lines.append(f"  StdDev: {stats['stdev']:.2f} ms")
            lines.append(f"  Count:  {stats['count']}")

        return "\n".join(lines)


@contextmanager
def measure_time(name: str = "operation", metrics: PerformanceMetrics = None):
    """Context manager to measure execution time."""
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        if metrics:
            metrics.record(name, duration_ms)
        print(f"⏱️  {name}: {duration_ms:.2f} ms")


def performance_test(max_time_ms: float = None, iterations: int = 10, warmup: int = 2):
    """
    Decorator for performance testing.

    Args:
        max_time_ms: Maximum allowed time in milliseconds (soft limit)
        iterations: Number of test iterations
        warmup: Number of warmup iterations (not counted)
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            timings = []

            # Warmup runs
            for _ in range(warmup):
                func(*args, **kwargs)

            # Measured runs
            for _ in range(iterations):
                start = time.perf_counter()
                result = func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                timings.append(duration_ms)

            # Calculate statistics
            mean_time = statistics.mean(timings)
            median_time = statistics.median(timings)
            max_time = max(timings)
            min_time = min(timings)

            print(f"\n📊 Performance results for {func.__name__}:")
            print(f"   Mean:   {mean_time:.2f} ms")
            print(f"   Median: {median_time:.2f} ms")
            print(f"   Min:    {min_time:.2f} ms")
            print(f"   Max:    {max_time:.2f} ms")

            if max_time_ms and mean_time > max_time_ms:
                print(
                    f"   ⚠️  Warning: Mean time {mean_time:.2f} ms exceeds target {max_time_ms} ms"
                )

            return result

        return wrapper

    return decorator


async def measure_async_time(name: str, coro, metrics: PerformanceMetrics = None):
    """Measure execution time of an async function."""
    start = time.perf_counter()
    result = await coro
    duration_ms = (time.perf_counter() - start) * 1000

    if metrics:
        metrics.record(name, duration_ms)

    print(f"⏱️  {name}: {duration_ms:.2f} ms")
    return result, duration_ms
