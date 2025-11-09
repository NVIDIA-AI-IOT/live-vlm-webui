# Testing Guide

This directory contains the test suite for the Live VLM WebUI project.

> 📖 **For comprehensive testing strategy and CI vs Local development guidelines, see:**
> **[`docs/development/testing.md`](../docs/development/testing.md)** - Complete testing guide with strategy, performance testing, and tiered workflows

## Quick Overview

- **Unit tests** (`tests/unit/`) - Fast, no dependencies - **Run in CI**
- **Integration tests** (`tests/integration/`) - Test component interactions - **Run in CI**
- **E2E tests** (`tests/e2e/`) - Full workflow with video recording - **Local only** (45-60s, requires GPU/Ollama)
- **Performance tests** (`tests/performance/`) - Real-time constraints - **Local only** (requires GPU)

## Directory Structure

```
tests/
├── unit/              # Unit tests for individual components
├── integration/       # Integration tests for component interactions
├── e2e/              # End-to-end workflow tests
├── performance/      # Performance and real-time constraint tests
├── fixtures/         # Test fixtures and test data
├── utils/            # Testing utilities and helpers
├── conftest.py       # Pytest configuration and shared fixtures
└── README.md         # This file
```

## Running Tests

### Quick Start

```bash
# Run all tests
pytest

# Run unit tests only (fast)
pytest tests/unit

# Run with coverage
pytest --cov=live_vlm_webui --cov-report=html

# Run performance tests
pytest -m performance
```

### Using Test Scripts

We provide convenient shell scripts in the `scripts/` directory:

```bash
# Quick test run (unit tests, no slow tests)
./scripts/test_quick.sh

# Full test suite with options
./scripts/run_tests.sh -u              # Unit tests only
./scripts/run_tests.sh -c              # With coverage
./scripts/run_tests.sh -m "not slow"   # Exclude slow tests

# Coverage report
./scripts/test_coverage.sh

# Performance tests
./scripts/run_performance_tests.sh

# Pre-commit checks (formatting, linting, tests)
./scripts/pre_commit_check.sh
```

## Test Markers

We use pytest markers to categorize and filter tests:

| Marker | Purpose | Skip with |
|--------|---------|-----------|
| `@pytest.mark.performance` | Performance/benchmark test | `-m "not performance"` |
| `@pytest.mark.slow` | Slow-running test | `-m "not slow"` |
| `@pytest.mark.e2e` | End-to-end test | `-m "not e2e"` |
| `@pytest.mark.asyncio` | Async test | (required for async) |

**Running with markers:**
```bash
# Only performance tests
pytest -m performance

# Exclude slow tests (fast feedback during development)
pytest -m "not slow"

# Only unit tests, no slow ones
pytest tests/unit -m "not slow"

# Run everything except E2E
pytest -m "not e2e"
```

## Test Categories

### Unit Tests (`tests/unit/`)

Test individual components in isolation with mocked dependencies.

**Examples:**
- `test_gpu_monitor.py` - GPU monitoring functions
- `test_vlm_service.py` - VLM service client
- `test_video_processor.py` - Video frame processing

**Run:**
```bash
pytest tests/unit -v
```

### Integration Tests (`tests/integration/`)

Test interactions between multiple components and HTTP endpoints.

**Examples:**
- `test_server.py` - Web server endpoints, static files
- `test_video_pipeline.py` - Video processing pipeline

**Run:**
```bash
pytest tests/integration -v
```

### End-to-End Tests (`tests/e2e/`)

Test complete workflows with a **real browser** (Playwright).

**What E2E tests catch:**
- ✅ Missing static files (images, CSS, JS)
- ✅ Browser console errors
- ✅ Screen flashing from failed requests
- ✅ Visual regressions

**Setup:**
```bash
pip install pytest-playwright
playwright install chromium
```

**Run:**
```bash
pytest tests/e2e -v

# With visible browser
pytest tests/e2e --headed
```

See `tests/e2e/README.md` for details.

### Performance Tests (`tests/performance/`)

Test real-time performance constraints with **regression detection**.

**Uses regression testing:**
- Tracks baseline performance (hardware-specific, stored locally)
- Detects when functions get >20% slower
- Hard limits for critical constraints (< 33ms for 30fps)

**Run:**
```bash
# Establish YOUR baseline (first time on your hardware)
./scripts/run_performance_tests.sh --save-baseline

# Check for regressions (after changes)
./scripts/run_performance_tests.sh

# Show YOUR current baseline
./scripts/run_performance_tests.sh --show-baseline
```

**Note:** Baselines are **hardware-specific** and stored locally (not in git). Each developer establishes their own baseline on their system.

See `REGRESSION_TESTING.md` for details.

## Performance Regression Testing

For real-time video processing at 30fps, we have ~33ms per frame. Instead of fixed time budgets, we use **regression detection**:

**How it works:**
1. Each developer establishes their own baseline on their hardware
2. Tests detect when functions get >20% slower than that baseline
3. Hard limits ensure critical constraints (< 33ms for 30fps)

**Example:**
```python
@pytest.mark.performance
def test_frame_resize_regression(regression_tracker):
    # Measure performance
    stats = measure_performance()

    # Check for regression (automatic!)
    regression = regression_tracker.check_regression(
        "video_processor.resize_frame",
        stats['mean'],
        stats['p95'],
        threshold_percent=20.0
    )
    # Outputs: ✅ Stable or ⚠️ Regression detected

    # Hard limit for real-time constraint
    assert stats['p95'] < 33.33, "Too slow for 30fps!"
```

**Workflow:**
```bash
# 1. Establish YOUR baseline (first time on your hardware)
./scripts/run_performance_tests.sh --save-baseline

# 2. After making changes
./scripts/run_performance_tests.sh
# Shows: ✅ Stable, ⚠️ Regression, or 🎉 Improvement

# 3. If regression: profile and fix
./scripts/profile_code.sh video_processor --visualize

# 4. Update YOUR baseline after intentional optimization
./scripts/run_performance_tests.sh --save-baseline
```

**Why regression testing?**
- ✅ Works on any hardware (each developer compares to their own baseline)
- ✅ Automatically detects slowdowns (>20% slower than your baseline)
- ✅ Still has hard limits for critical paths (absolute requirements)
- ✅ Tracks improvements too!

**Important:** Baselines are stored locally in `.pytest_cache/` and are **not committed to git**. This allows each developer to test against their own hardware's performance characteristics.

See `REGRESSION_TESTING.md` and `docs/performance-regression-testing.md` for details.

## Writing Tests

### Unit Test Example

```python
import pytest
from unittest.mock import Mock, patch

class TestMyComponent:
    """Test MyComponent functionality."""

    def test_basic_functionality(self):
        """Test basic operation."""
        component = MyComponent()
        result = component.do_something()
        assert result is not None

    @pytest.mark.asyncio
    async def test_async_operation(self):
        """Test async operation."""
        component = MyComponent()
        result = await component.async_operation()
        assert result == expected_value
```

### Performance Test Example

```python
import pytest
import time
from tests.utils.performance import PerformanceMetrics

@pytest.mark.performance
def test_operation_speed(performance_metrics):
    """Test operation meets performance requirements."""
    component = MyComponent()

    for _ in range(100):
        start = time.perf_counter()
        component.fast_operation()
        duration_ms = (time.perf_counter() - start) * 1000
        performance_metrics.record("fast_operation", duration_ms)

    stats = performance_metrics.get_stats("fast_operation")
    assert stats['mean'] < 10.0  # Must be under 10ms
```

## Continuous Integration

Tests run automatically on GitHub Actions for:
- Every push to `main` or `feature/*` branches
- Every pull request to `main`

The CI pipeline includes:
- Unit tests (multiple Python versions)
- Integration tests
- Code coverage
- Performance tests (informational)
- Code quality checks (black, ruff, mypy)

See `.github/workflows/tests.yml` for details.

## Test Coverage

We aim for:
- **Overall coverage**: > 70%
- **Critical paths**: > 90%

**Generate coverage report:**

```bash
pytest --cov=live_vlm_webui --cov-report=html
open htmlcov/index.html
```

## Troubleshooting

### Tests are slow

```bash
# Run only fast tests
pytest -m "not slow"

# Run in parallel (requires pytest-xdist)
pip install pytest-xdist
pytest -n auto
```

### Performance tests failing

Performance tests may fail on slower hardware. Consider:
- Running on dedicated hardware
- Adjusting `PerformanceConstraints` in `tests/utils/performance.py`
- Using performance tests as benchmarks, not gate-keepers

### Import errors

Make sure the package is installed in development mode:

```bash
pip install -e .
pip install -r requirements-dev.txt
```

## Contributing

When adding new features:
1. Write unit tests for new functions
2. Add integration tests for new workflows
3. Add performance tests for time-critical code
4. Run pre-commit checks: `./scripts/pre_commit_check.sh`
5. Ensure coverage doesn't decrease

## Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [pytest-benchmark](https://pytest-benchmark.readthedocs.io/)
- [Performance testing best practices](https://docs.python.org/3/library/profile.html)

