"""Shared pytest fixtures and configuration for all tests."""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock

from tests.utils.performance import PerformanceMetrics

# Test data directory
TEST_DATA_DIR = Path(__file__).parent / "fixtures" / "data"


# Register custom markers
def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line("markers", "performance: mark test as a performance test (can be slow)")
    config.addinivalue_line(
        "markers", "slow: mark test as slow running (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_vlm_service():
    """Mock VLM service for testing without actual model calls."""
    service = AsyncMock()
    service.query_async.return_value = {
        "response": "Test response from VLM",
        "model": "test-model",
    }
    service.is_ready.return_value = True
    return service


@pytest.fixture
def mock_gpu_monitor():
    """Mock GPU monitor for testing without actual GPU."""
    monitor = Mock()
    monitor.get_stats.return_value = {
        "gpu_utilization": 45,
        "memory_used": 2048,
        "memory_total": 8192,
        "temperature": 65,
    }
    return monitor


@pytest.fixture
def sample_image_path():
    """Path to a sample test image."""
    return TEST_DATA_DIR / "sample_image.jpg"


@pytest.fixture
def sample_video_path():
    """Path to a sample test video."""
    return TEST_DATA_DIR / "sample_video.mp4"


@pytest.fixture
async def test_server():
    """Create a test server instance."""
    from live_vlm_webui.server import create_app

    app = await create_app(test_mode=True)

    # You may need to adjust this based on your server implementation
    yield app

    # Cleanup
    await app.cleanup()


@pytest.fixture
def mock_video_processor():
    """Mock video processor for testing."""
    processor = AsyncMock()
    processor.process_frame.return_value = {
        "timestamp": 0.0,
        "frame_number": 1,
        "status": "processed",
    }
    return processor


@pytest.fixture
def performance_metrics():
    """Provide a PerformanceMetrics instance for tests."""
    return PerformanceMetrics()


@pytest.fixture(scope="session")
def performance_report(request):
    """Collect performance metrics across all tests and report at end."""
    metrics = PerformanceMetrics()

    yield metrics

    # Print summary at end of test session
    if metrics.timings:
        print("\n" + "=" * 70)
        print("PERFORMANCE TEST SUMMARY")
        print("=" * 70)
        print(metrics.summary())
