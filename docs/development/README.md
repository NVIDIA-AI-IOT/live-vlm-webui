# Developer Documentation

This directory contains documentation for developers working on the project.

## Testing Documentation

### Quick Start
- **`testing-quickstart.md`** - Quick reference for common testing commands
- **`regression-testing-quickref.md`** - Quick reference for performance regression testing

### Comprehensive Guides
- **`testing.md`** - Complete testing and performance guide
- **`performance-regression-testing.md`** - Performance regression testing strategy
- **`multi-hardware-testing.md`** - Testing across different hardware (Jetson, PC, Mac)

## Quick Commands

```bash
# Quick tests during development
./scripts/test_quick.sh

# All tests with coverage
./scripts/run_tests.sh -c

# Performance regression tests
./scripts/run_performance_tests.sh

# Before commit
./scripts/pre_commit_check.sh
```

## Related Documentation

- **`../setup/`** - Setup guides for different platforms
- **`../../tests/README.md`** - Testing infrastructure and organization
- **`../../tests/e2e/README.md`** - End-to-end testing with Playwright
- **`../../CONTRIBUTING.md`** - Contribution guidelines

## Development Workflow

1. **During development**: `./scripts/test_quick.sh`
2. **Before commit**: `./scripts/pre_commit_check.sh`
3. **Check performance**: `./scripts/run_performance_tests.sh`
4. **Profile if needed**: `./scripts/profile_code.sh component`

See `testing-quickstart.md` for more details.

