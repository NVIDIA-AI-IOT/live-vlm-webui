# Testing Quick Start Guide

## One-Line Commands

```bash
# 🚀 Quick tests (fastest, unit tests only)
./scripts/test_quick.sh

# 🧪 All tests
pytest

# 📊 Tests with coverage
./scripts/test_coverage.sh

# ⚡ Performance tests
./scripts/run_performance_tests.sh

# ✅ Pre-commit checks (run before committing)
./scripts/pre_commit_check.sh
```

## Test Scripts Overview

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `test_quick.sh` | Fast unit tests only | During development, before each commit |
| `run_tests.sh` | Flexible test runner with options | Custom test runs, CI |
| `test_coverage.sh` | Generate coverage report | Before PR, check test coverage |
| `run_performance_tests.sh` | Performance benchmarks | After optimization, before release |
| `pre_commit_check.sh` | Format + lint + test | Before committing code |
| `profile_code.sh` | Profile for bottlenecks | When optimizing performance |

## Common Workflows

### During Development

```bash
# Quick check after making changes
./scripts/test_quick.sh

# Full check before committing
./scripts/pre_commit_check.sh
```

### Before Pull Request

```bash
# Run full test suite with coverage
./scripts/run_tests.sh -c

# Check performance
./scripts/run_performance_tests.sh

# Ensure code quality
black src/ tests/
ruff check --fix src/ tests/
```

### Optimizing Performance

```bash
# 1. Run performance tests to identify slow code
./scripts/run_performance_tests.sh

# 2. Profile the slow component
./scripts/profile_code.sh video_processor --visualize

# 3. Make optimizations

# 4. Re-run performance tests
./scripts/run_performance_tests.sh

# 5. Save baseline if improved
./scripts/run_performance_tests.sh --save-baseline
```

### Debugging Test Failures

```bash
# Run with verbose output
pytest tests/unit/test_video_processor.py -v -s

# Run single test
pytest tests/unit/test_video_processor.py::TestVideoProcessor::test_frame_resize -v

# Run with debugger on failure
pytest --pdb tests/unit/test_video_processor.py

# Show locals on failure
pytest --showlocals tests/unit/test_video_processor.py
```

## pytest Commands

### Basic Usage

```bash
# Run all tests
pytest

# Run specific directory
pytest tests/unit

# Run specific file
pytest tests/unit/test_video_processor.py

# Run specific test
pytest tests/unit/test_video_processor.py::TestVideoProcessor::test_frame_resize
```

### With Markers

```bash
# Run performance tests only
pytest -m performance

# Exclude slow tests
pytest -m "not slow"

# Run unit tests without slow ones
pytest tests/unit -m "not slow"
```

### With Options

```bash
# Verbose output
pytest -v

# Show print statements
pytest -s

# Stop on first failure
pytest -x

# Fail after N failures
pytest --maxfail=3

# Run last failed tests
pytest --lf

# Run failed tests first
pytest --ff

# Run in parallel (requires pytest-xdist)
pytest -n auto
```

### Coverage

```bash
# Basic coverage
pytest --cov=live_vlm_webui

# HTML report
pytest --cov=live_vlm_webui --cov-report=html

# Missing lines
pytest --cov=live_vlm_webui --cov-report=term-missing

# Multiple reports
pytest --cov=live_vlm_webui \
       --cov-report=html \
       --cov-report=term-missing \
       --cov-report=json
```

## Performance Testing

### Run Performance Tests

```bash
# All performance tests
pytest -m performance -v -s

# Specific performance test
pytest tests/performance/test_realtime_constraints.py -v -s

# Save as baseline
./scripts/run_performance_tests.sh --save-baseline

# Compare with baseline
./scripts/run_performance_tests.sh --compare
```

### Profile Code

```bash
# Profile video processor
./scripts/profile_code.sh video_processor

# Profile with visualization
./scripts/profile_code.sh video_processor --visualize

# Manual profiling
python -m cProfile -o profile.stats \
    -m pytest tests/unit/test_video_processor.py::TestVideoProcessorPerformance

python -m pstats profile.stats
```

## Code Quality

### Formatting

```bash
# Check formatting
black --check src/ tests/

# Fix formatting
black src/ tests/

# Check with diff
black --diff src/ tests/
```

### Linting

```bash
# Check for issues
ruff check src/ tests/

# Fix automatically
ruff check --fix src/ tests/

# Show all issues
ruff check --output-format=full src/ tests/
```

### Type Checking

```bash
# Check types
mypy src/

# With specific config
mypy src/ --ignore-missing-imports --strict
```

## Environment Setup

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Install package in editable mode
pip install -e .

# Verify installation
python -c "import live_vlm_webui; print(live_vlm_webui.__version__)"
pytest --version
```

## Troubleshooting

### Tests not found

```bash
# Check pytest can find tests
pytest --collect-only

# Install package in editable mode
pip install -e .
```

### Import errors

```bash
# Ensure dependencies are installed
pip install -r requirements-dev.txt

# Reinstall package
pip install -e . --force-reinstall
```

### Performance tests failing

```bash
# Run without assertions (just measure)
pytest tests/performance -v -s --no-assert

# Check available hardware
python -c "import cv2; print(cv2.getBuildInformation())"

# Run on fewer iterations
# (Edit test file temporarily)
```

### Scripts not executable

```bash
# Make scripts executable
chmod +x scripts/*.sh

# Or run with bash
bash scripts/test_quick.sh
```

## CI/CD Integration

The test suite runs automatically on GitHub Actions:

```yaml
# See .github/workflows/tests.yml
- Unit tests (Python 3.10, 3.11, 3.12)
- Integration tests
- Coverage report
- Performance tests (informational)
- Code quality checks
```

**Local CI simulation:**

```bash
# Run all checks like CI does
./scripts/pre_commit_check.sh
./scripts/run_tests.sh -c
./scripts/run_performance_tests.sh
```

## Best Practices

1. ✅ Run `test_quick.sh` frequently during development
2. ✅ Run `pre_commit_check.sh` before committing
3. ✅ Run full test suite with coverage before PR
4. ✅ Add performance tests for time-critical code
5. ✅ Profile before optimizing (measure, don't guess)
6. ✅ Use markers to organize tests
7. ✅ Write clear test names that describe what they test
8. ✅ Keep tests fast (mock slow dependencies)

## Getting Help

- **Test documentation**: `tests/README.md`
- **Performance guide**: `docs/TESTING.md`
- **pytest docs**: https://docs.pytest.org/
- **Coverage docs**: https://coverage.readthedocs.io/

## Summary

**For quick development:**
```bash
./scripts/test_quick.sh
```

**Before committing:**
```bash
./scripts/pre_commit_check.sh
```

**For performance work:**
```bash
./scripts/run_performance_tests.sh
./scripts/profile_code.sh video_processor
```

That's it! Happy testing! 🧪🚀

