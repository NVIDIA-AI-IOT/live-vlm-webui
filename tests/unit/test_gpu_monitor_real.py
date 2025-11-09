"""Real unit tests for GPU monitor - matches actual implementation."""


class TestGPUMonitorFactory:
    """Test the GPU monitor factory function."""

    def test_create_monitor_returns_monitor(self):
        """Test that create_monitor returns a GPUMonitor instance."""
        from live_vlm_webui.gpu_monitor import create_monitor, GPUMonitor

        # This will auto-detect or fall back to NVMLMonitor
        monitor = create_monitor()

        assert monitor is not None
        assert isinstance(monitor, GPUMonitor)
        print(f"✅ Got monitor: {type(monitor).__name__}")

    def test_create_monitor_apple_silicon(self):
        """Test creating Apple Silicon monitor."""
        from live_vlm_webui.gpu_monitor import create_monitor, AppleSiliconMonitor

        monitor = create_monitor(platform="apple")

        assert monitor is not None
        assert isinstance(monitor, AppleSiliconMonitor)
        print("✅ Created AppleSiliconMonitor")

    def test_get_stats_returns_dict(self):
        """Test that get_stats returns a dictionary."""
        from live_vlm_webui.gpu_monitor import create_monitor

        monitor = create_monitor()
        stats = monitor.get_stats()

        assert isinstance(stats, dict)
        assert "platform" in stats
        assert "cpu_percent" in stats
        assert "ram_used_gb" in stats
        print(f"✅ Stats keys: {list(stats.keys())}")


class TestGetCPUModel:
    """Test the get_cpu_model utility function."""

    def test_get_cpu_model_returns_string(self):
        """Test that get_cpu_model returns a string."""
        from live_vlm_webui.gpu_monitor import get_cpu_model

        cpu_model = get_cpu_model()

        assert isinstance(cpu_model, str)
        assert len(cpu_model) > 0
        print(f"✅ CPU Model: {cpu_model}")
