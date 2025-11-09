"""Performance regression testing utilities."""

import json
import time
import functools
from pathlib import Path
from typing import Dict, Optional, Callable, Any
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class PerformanceBaseline:
    """Store baseline performance metrics."""

    function_name: str
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    sample_size: int
    timestamp: str
    hardware_info: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict):
        """Create from dictionary."""
        return cls(**data)


class RegressionTracker:
    """Track and detect performance regressions."""

    def __init__(self, baseline_file: str = ".performance_baseline.json"):
        self.baseline_file = Path(baseline_file)
        self.baselines: Dict[str, PerformanceBaseline] = {}
        self.load_baselines()

    def load_baselines(self):
        """Load baselines from file."""
        if self.baseline_file.exists():
            try:
                with open(self.baseline_file, "r") as f:
                    data = json.load(f)
                    self.baselines = {
                        name: PerformanceBaseline.from_dict(baseline)
                        for name, baseline in data.items()
                    }
            except Exception as e:
                print(f"Warning: Could not load baselines: {e}")

    def save_baselines(self):
        """Save baselines to file."""
        try:
            data = {name: baseline.to_dict() for name, baseline in self.baselines.items()}
            with open(self.baseline_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save baselines: {e}")

    def set_baseline(
        self,
        function_name: str,
        mean_ms: float,
        median_ms: float,
        p95_ms: float,
        p99_ms: float,
        sample_size: int,
        hardware_info: Optional[str] = None,
    ):
        """Set or update baseline for a function."""
        baseline = PerformanceBaseline(
            function_name=function_name,
            mean_ms=mean_ms,
            median_ms=median_ms,
            p95_ms=p95_ms,
            p99_ms=p99_ms,
            sample_size=sample_size,
            timestamp=datetime.now().isoformat(),
            hardware_info=hardware_info,
        )
        self.baselines[function_name] = baseline

    def check_regression(
        self,
        function_name: str,
        current_mean: float,
        current_p95: float,
        threshold_percent: float = 20.0,
    ) -> Dict[str, Any]:
        """
        Check if current performance is a regression from baseline.

        Args:
            function_name: Name of the function
            current_mean: Current mean execution time (ms)
            current_p95: Current P95 execution time (ms)
            threshold_percent: Percentage threshold for regression (default 20%)

        Returns:
            Dictionary with regression analysis
        """
        if function_name not in self.baselines:
            return {
                "has_baseline": False,
                "is_regression": False,
                "message": "No baseline found. Run with --save-baseline to establish baseline.",
            }

        baseline = self.baselines[function_name]

        # Calculate percentage changes
        mean_change_pct = ((current_mean - baseline.mean_ms) / baseline.mean_ms) * 100
        p95_change_pct = ((current_p95 - baseline.p95_ms) / baseline.p95_ms) * 100

        # Check if regression
        is_regression = mean_change_pct > threshold_percent or p95_change_pct > threshold_percent

        # Check if improvement
        is_improvement = (
            mean_change_pct < -threshold_percent and p95_change_pct < -threshold_percent
        )

        result = {
            "has_baseline": True,
            "is_regression": is_regression,
            "is_improvement": is_improvement,
            "baseline": {
                "mean_ms": baseline.mean_ms,
                "p95_ms": baseline.p95_ms,
                "timestamp": baseline.timestamp,
            },
            "current": {
                "mean_ms": current_mean,
                "p95_ms": current_p95,
            },
            "change": {
                "mean_percent": mean_change_pct,
                "p95_percent": p95_change_pct,
                "mean_ms": current_mean - baseline.mean_ms,
                "p95_ms": current_p95 - baseline.p95_ms,
            },
            "threshold_percent": threshold_percent,
        }

        # Generate message
        if is_regression:
            result["message"] = (
                f"⚠️  PERFORMANCE REGRESSION DETECTED!\n"
                f"   Function: {function_name}\n"
                f"   Mean: {baseline.mean_ms:.2f} → {current_mean:.2f} ms "
                f"({mean_change_pct:+.1f}%)\n"
                f"   P95:  {baseline.p95_ms:.2f} → {current_p95:.2f} ms "
                f"({p95_change_pct:+.1f}%)\n"
                f"   Threshold: {threshold_percent}%"
            )
        elif is_improvement:
            result["message"] = (
                f"🎉 PERFORMANCE IMPROVEMENT!\n"
                f"   Function: {function_name}\n"
                f"   Mean: {baseline.mean_ms:.2f} → {current_mean:.2f} ms "
                f"({mean_change_pct:+.1f}%)\n"
                f"   P95:  {baseline.p95_ms:.2f} → {current_p95:.2f} ms "
                f"({p95_change_pct:+.1f}%)"
            )
        else:
            result["message"] = (
                f"✅ Performance stable (within {threshold_percent}% of baseline)\n"
                f"   Mean: {baseline.mean_ms:.2f} → {current_mean:.2f} ms "
                f"({mean_change_pct:+.1f}%)\n"
                f"   P95:  {baseline.p95_ms:.2f} → {current_p95:.2f} ms "
                f"({p95_change_pct:+.1f}%)"
            )

        return result

    def get_summary(self) -> str:
        """Get summary of all baselines."""
        if not self.baselines:
            return "No performance baselines recorded."

        lines = ["Performance Baselines:", "=" * 60]

        for name, baseline in sorted(self.baselines.items()):
            lines.append(f"\n{name}:")
            lines.append(f"  Mean:   {baseline.mean_ms:.2f} ms")
            lines.append(f"  P95:    {baseline.p95_ms:.2f} ms")
            lines.append(f"  P99:    {baseline.p99_ms:.2f} ms")
            lines.append(f"  Samples: {baseline.sample_size}")
            lines.append(f"  Recorded: {baseline.timestamp[:19]}")

        return "\n".join(lines)


def regression_test(
    tracker: RegressionTracker,
    function_name: str,
    iterations: int = 10,
    warmup: int = 2,
    threshold_percent: float = 20.0,
    fail_on_regression: bool = True,
):
    """
    Decorator for performance regression testing.

    Args:
        tracker: RegressionTracker instance
        function_name: Name to track this function by
        iterations: Number of test iterations
        warmup: Number of warmup iterations
        threshold_percent: Percentage threshold for regression
        fail_on_regression: Whether to raise assertion on regression
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            import statistics

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
            mean_ms = statistics.mean(timings)
            median_ms = statistics.median(timings)
            p95_ms = statistics.quantiles(timings, n=20)[18] if len(timings) > 1 else timings[0]
            p99_ms = statistics.quantiles(timings, n=100)[98] if len(timings) > 1 else timings[0]

            print(f"\n📊 Performance: {function_name}")
            print(f"   Mean:   {mean_ms:.2f} ms")
            print(f"   Median: {median_ms:.2f} ms")
            print(f"   P95:    {p95_ms:.2f} ms")
            print(f"   P99:    {p99_ms:.2f} ms")

            # Check for regression
            regression_result = tracker.check_regression(
                function_name, mean_ms, p95_ms, threshold_percent
            )

            print(f"\n{regression_result['message']}")

            if fail_on_regression and regression_result["is_regression"]:
                raise AssertionError(
                    f"Performance regression detected: {function_name} "
                    f"Mean +{regression_result['change']['mean_percent']:.1f}%, "
                    f"P95 +{regression_result['change']['p95_percent']:.1f}%"
                )

            return result

        return wrapper

    return decorator


def get_hardware_info() -> str:
    """Get hardware information for baseline context."""
    import platform

    try:
        import psutil

        cpu_info = f"{psutil.cpu_count()} cores"
        mem_info = f"{psutil.virtual_memory().total / 1024**3:.1f}GB RAM"
    except ImportError:
        cpu_info = "unknown"
        mem_info = "unknown"

    return f"{platform.machine()} | {cpu_info} | {mem_info}"
