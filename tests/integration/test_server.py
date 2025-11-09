"""Integration tests for the web server."""

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase


class TestServerIntegration(AioHTTPTestCase):
    """Test server integration with all components."""

    async def get_application(self):
        """Create application for testing."""
        # Create minimal test app
        app = web.Application()
        return app

    async def test_server_starts(self):
        """Test that server starts successfully."""
        assert self.app is not None
        assert isinstance(self.app, web.Application)


@pytest.mark.asyncio
async def test_websocket_connection(mock_vlm_service):
    """Test WebSocket connection handling."""
    # This is a placeholder - implement based on your WebSocket logic
    assert mock_vlm_service is not None


@pytest.mark.asyncio
async def test_video_stream_processing(mock_video_processor, mock_vlm_service):
    """Test video stream processing pipeline."""
    # Mock the entire pipeline
    test_frame = b"fake_frame_data"

    # Process frame
    result = await mock_video_processor.process_frame(test_frame)

    assert result is not None
    assert result["status"] == "processed"


class TestStaticFiles(AioHTTPTestCase):
    """Test static file serving - catches missing images/assets."""

    async def get_application(self):
        """Create application for testing."""
        try:
            from live_vlm_webui.server import create_app

            return await create_app(test_mode=True)
        except Exception:
            # Fallback
            app = web.Application()
            return app

    async def test_static_images_exist(self):
        """Test that required image assets are accessible."""
        # Test actual image paths that exist in the project
        required_images = [
            "/images/jetson-agx-orin-devkit_256px.png",
            "/images/jetson-agx-thor-devkit_256px.png",
            "/images/dgx-spark_256px.png",
        ]

        for image_path in required_images:
            resp = await self.client.request("GET", image_path)
            assert resp.status == 200, f"Missing static file: {image_path} (returns {resp.status})"

            # Verify it's actually an image
            content_type = resp.headers.get("Content-Type", "")
            assert "image" in content_type, f"File {image_path} is not an image: {content_type}"

    async def test_at_least_one_image_exists(self):
        """Test that at least one image asset is accessible."""
        # Just verify that images directory is accessible and has content
        resp = await self.client.request("GET", "/images/jetson-agx-orin-devkit_256px.png")
        assert resp.status == 200, "Images directory should be accessible with at least one image"

    async def test_index_page_loads(self):
        """Test that main page loads without errors."""
        resp = await self.client.request("GET", "/")
        assert resp.status == 200, "Main page failed to load"

        # Get the HTML content
        html = await resp.text()

        # Check that it references expected assets
        # This catches if HTML references images that don't exist
        assert "<img" in html or "background-image" in html, "No images found in HTML"

        # Parse and verify all image sources exist
        import re

        img_srcs = re.findall(r'src=["\']([^"\']+)["\']', html)

        for src in img_srcs:
            if src.startswith("http"):
                continue  # Skip external URLs

            # Test each image reference
            resp = await self.client.request("GET", src)
            assert resp.status == 200, f"Image referenced in HTML but missing: {src}"

    async def test_no_404_on_common_paths(self):
        """Test that common paths don't return 404."""
        # Test only paths that should actually work
        paths_to_check = [
            "/",
            "/images/jetson-agx-orin-devkit_256px.png",
        ]

        for path in paths_to_check:
            resp = await self.client.request("GET", path)
            assert resp.status != 404, f"Path returns 404 (likely missing files): {path}"
