# End-to-End Browser Tests

## Overview

E2E tests use **real browsers** (via Playwright) to catch issues that integration tests cannot:

✅ **Missing images** - Would have caught `/images/` directory not copied to Docker
✅ **Browser console errors** - JavaScript errors, failed requests
✅ **Screen flashing** - Repeated failed image loads causing flickering
✅ **Visual regressions** - UI actually renders correctly
✅ **Network issues** - 404s, failed asset loading

## Two Types of E2E Tests

### 1. **Quick Smoke Tests** (`test_browser.py`)
- Fast (<1 second each)
- No camera or VLM required
- Run in CI automatically
- Check UI loads without crashes

### 2. **Real Workflow Tests** (`test_real_workflow.py`) ⭐ NEW!
- Full end-to-end (~30 seconds each)
- Uses **real video input** and **actual VLM inference**
- Creates interesting 30-second videos showing real usage
- **Only runs locally** (skipped in CI - no GPU)
- See [REAL_WORKFLOW_TESTING.md](./REAL_WORKFLOW_TESTING.md) for details

## The Problem E2E Tests Solve

**Your Docker issue:**
```
Problem: /images/ directory not copied into Docker container
Symptom: Browser kept requesting images, screen kept flashing
Integration test: ❌ Didn't catch it (just HTTP-level)
E2E test: ✅ Would catch it (real browser sees missing images)
```

## Setup

```bash
# Install Playwright
pip install pytest-playwright

# Install browsers
playwright install chromium

# Or just Chrome
playwright install chromium
```

## Running E2E Tests

```bash
# Run all E2E tests
pytest tests/e2e -v

# Run with browser visible (headed mode)
pytest tests/e2e --headed

# Run specific test
pytest tests/e2e/test_browser.py::test_no_missing_images -v

# Exclude slow E2E tests
pytest -m "not e2e"
```

## Test Types

### 1. Static Asset Tests

**`test_no_missing_images()`** - Catches missing `/images/` directory
```python
# Checks all <img> tags actually load
# Would have caught your Docker issue!
```

**`test_all_static_assets_load()`** - CSS, JS, images
```python
# Verifies no 404s on any static files
```

### 2. Console Error Tests

**`test_page_loads_without_errors()`** - Browser console errors
```python
# Catches JavaScript errors
# Catches failed network requests
```

### 3. Flashing Screen Tests

**`test_no_flashing_screen()`** - Repeated failed requests
```python
# Tracks if images are requested multiple times
# Would catch the "screen kept flashing" issue
```

**`test_no_404_in_network_requests()`** - All requests succeed
```python
# Any 404 fails the test
```

### 4. Docker-Specific Tests

**`test_docker_container_has_images()`** - Docker image completeness
```python
# Specifically tests /images/ directory exists
# Add this to CI to prevent shipping broken Docker images
```

## Integration vs E2E

| Feature | Integration Test | E2E Test (Browser) |
|---------|-----------------|-------------------|
| Speed | ⚡ Fast (ms) | 🐢 Slow (seconds) |
| Real browser | ❌ No | ✅ Yes |
| Catches missing images | ❌ Maybe | ✅ Always |
| Catches console errors | ❌ No | ✅ Yes |
| Catches flashing | ❌ No | ✅ Yes |
| Catches JS errors | ❌ No | ✅ Yes |
| Run in CI | ✅ Always | ⚠️ Optionally |

## Example: How E2E Catches Your Docker Issue

**Your Docker issue:**
```dockerfile
# WRONG - images/ not copied
FROM python:3.12
COPY src/ /app/src/
# ❌ Forgot: COPY images/ /app/images/
```

**Integration test (didn't catch it):**
```python
async def test_health_endpoint(self):
    resp = await self.client.request("GET", "/health")
    assert resp.status == 200  # ✅ Passes (health endpoint works)
    # But images are still missing!
```

**E2E test (would catch it):**
```python
def test_no_missing_images(page):
    page.goto("http://localhost:8080")
    images = page.query_selector_all("img")

    for img in images:
        if img.evaluate("el => el.naturalWidth") == 0:
            pytest.fail(f"Image failed to load: {img.get_attribute('src')}")
    # ❌ Fails: /images/logo.png has naturalWidth=0
```

**E2E test for flashing:**
```python
def test_no_flashing_screen(page):
    image_requests = {}
    page.on("request", lambda req: track_requests(req, image_requests))
    page.goto("http://localhost:8080")
    page.wait_for_timeout(3000)

    # Check if any image requested > 2 times (reload loop)
    repeated = {url: count for url, count in image_requests.items() if count > 2}
    assert len(repeated) == 0  # ❌ Fails: logo.png requested 47 times!
```

## CI Integration

### Quick CI (Integration Only)
```yaml
# Fast, runs on every commit
- name: Integration Tests
  run: pytest tests/integration -v
```

### Full CI (With E2E)
```yaml
# Slower, runs on PR
- name: E2E Tests
  run: |
    playwright install chromium
    pytest tests/e2e -v --headed=false
```

### Docker-Specific CI
```yaml
# Test Docker image has all files
- name: Build Docker Image
  run: docker build -t myapp .

- name: Test Docker Image
  run: |
    docker run -d -p 8080:8080 myapp
    playwright install chromium
    pytest tests/e2e/test_browser.py::test_docker_container_has_images -v
```

## Best Practices

### 1. Use Both Test Types

```bash
# Fast feedback during development
pytest tests/integration  # Runs in < 1 second

# Comprehensive check before commit
pytest tests/e2e  # Runs in ~30 seconds
```

### 2. Mark Slow Tests

```python
@pytest.mark.e2e
@pytest.mark.slow
def test_no_flashing_screen(page):
    # ...
```

```bash
# Skip slow tests during development
pytest -m "not slow"

# Run everything before commit
pytest
```

### 3. Record Videos on Failure

Playwright automatically records videos when tests fail:

```python
@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "record_video_dir": "test-results/videos/",
    }
```

Then watch: `test-results/videos/test_name.webm`

### 4. Take Screenshots on Failure

```python
def test_page_loads(page):
    try:
        page.goto("http://localhost:8080")
        assert page.is_visible("video")
    except AssertionError:
        page.screenshot(path="test-results/failure.png")
        raise
```

## Debugging E2E Tests

### Run with Visible Browser
```bash
pytest tests/e2e --headed --slowmo=1000
```

### Interactive Debug Mode
```bash
pytest tests/e2e --headed --pdb
# Browser stays open when test fails
```

### Playwright Inspector
```bash
PWDEBUG=1 pytest tests/e2e/test_browser.py::test_no_missing_images
# Step through test with visual debugger
```

## Common Issues

### "Browser not installed"
```bash
# Solution
playwright install chromium
```

### "Connection refused"
```bash
# Solution: Start server first
./scripts/start_server.sh &
pytest tests/e2e
```

### Tests timeout
```python
# Increase timeout
page.set_default_timeout(10000)  # 10 seconds
```

## Summary

**Use E2E tests to catch:**
- ✅ Missing static files (images, CSS, JS)
- ✅ Browser console errors
- ✅ Screen flashing from failed requests
- ✅ Visual regressions
- ✅ Docker image completeness

**Your specific issue:**
```python
# This test would have caught the missing /images/ directory
def test_docker_container_has_images(page):
    response = page.goto("http://localhost:8080/images/logo.png")
    assert response.status == 200  # ❌ Would fail with 404
```

Run E2E tests before releasing Docker images or Python wheels!

