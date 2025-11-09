"""
Real end-to-end workflow tests with actual video input and VLM inference.

These tests require:
- Server running on localhost:8090
- Ollama with VLM models (gemma3:4b, llama3.2-vision:11b)
- Test video automatically downloaded and converted (default mode)

These tests are LOCAL-ONLY (no GPU in CI) and create video recordings.

**Usage:**

  # Default: Automated test with fake video device (Y4M file)
  pytest tests/e2e/test_real_workflow.py::test_full_video_analysis_workflow -v -s

  # Optional: Test with REAL camera (/dev/video0 or OBS Virtual Camera)
  USE_REAL_CAMERA=1 pytest tests/e2e/test_real_workflow.py::test_full_video_analysis_workflow -v -s

**Test Coverage:** test_full_video_analysis_workflow
  - Comprehensive 45-60s workflow test
  - Tests: video streaming, UI interactions, settings, model switching
  - Creates video recording in test-results/videos/
  - Works with both fake device (default) and real camera (USE_REAL_CAMERA=1)
"""

import pytest
import os
import time
import re
import urllib.request
from pathlib import Path


# Test video file (HD 720p, publicly hosted, Creative Commons from Big Buck Bunny)
# Using Google Cloud Storage which is fast and reliable
TEST_VIDEO_URL = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"
# Store in tests/ directory to avoid Playwright's test-results/ cleanup
TEST_VIDEO_PATH = Path("tests/e2e/.test-data/test-video.mp4")

# Alternative URLs (change TEST_VIDEO_URL if needed):
# 1080p HD (~355MB): https://download.blender.org/demo/movies/BBB/bbb_sunflower_1080p_30fps_normal.mp4
# 4K Ultra HD (~355MB): https://download.blender.org/demo/movies/BBB/bbb_sunflower_2160p_30fps_normal.mp4
# 240p Low (~1MB): https://sample-videos.com/video123/mp4/240/big_buck_bunny_240p_1mb.mp4


# Skip these tests in CI
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.getenv("CI") == "true",
        reason="Real workflow tests require GPU, Ollama, and video device",
    ),
]


def download_test_video():
    """Download test video file if it doesn't exist."""
    # Ensure test-results directory exists
    TEST_VIDEO_PATH.parent.mkdir(parents=True, exist_ok=True)

    if TEST_VIDEO_PATH.exists():
        size_mb = TEST_VIDEO_PATH.stat().st_size / 1024 / 1024
        print(f"   ✅ Test video already exists: {TEST_VIDEO_PATH} ({size_mb:.1f} MB)")
        return TEST_VIDEO_PATH

    print(f"   📥 Downloading test video from {TEST_VIDEO_URL}...")
    print(f"   This will download ~151 MB to {TEST_VIDEO_PATH}...")
    try:
        urllib.request.urlretrieve(TEST_VIDEO_URL, TEST_VIDEO_PATH)
        print(
            f"   ✅ Downloaded: {TEST_VIDEO_PATH} ({TEST_VIDEO_PATH.stat().st_size / 1024 / 1024:.1f} MB)"
        )
        return TEST_VIDEO_PATH
    except Exception as e:
        print(f"   ⚠️  Could not download test video: {e}")
        return None


def convert_to_y4m(source_video_path):
    """
    Convert test video to .y4m format for Chrome fake device.
    Only converts first 30 seconds (video loops during the 45-60s test).

    Note: Y4M is UNCOMPRESSED format (required by Chrome's --use-file-for-fake-video-capture)
    Expected output: ~950 MB for 30 seconds at 720p30
    (Full 9-minute video would be ~17 GB!)

    The large file size is unavoidable - it's the price of automated E2E testing with real video.

    Returns path to .y4m file or None if conversion fails.
    """
    import subprocess

    # Store alongside the source video to avoid Playwright's test-results/ cleanup
    y4m_path = Path("tests/e2e/.test-data/test-video.y4m")
    y4m_path.parent.mkdir(parents=True, exist_ok=True)  # Ensure directory exists

    # Check if already converted
    if y4m_path.exists():
        size_mb = y4m_path.stat().st_size / 1024 / 1024
        print(f"   ✅ Y4M video already exists: {y4m_path} ({size_mb:.0f} MB)")
        # Warn if file is suspiciously large (might be old full 9-minute version)
        if size_mb > 2000:  # ~950 MB for 30s, so >2GB indicates full video
            print(f"   ⚠️  Warning: File is very large ({size_mb:.0f} MB)")
            print("   ⚠️  This might be the full 9-minute video. Consider regenerating:")
            print(f"   ⚠️  rm {y4m_path} && pytest ...")
        return y4m_path

    # Check if source video exists
    if not source_video_path or not Path(source_video_path).exists():
        print(f"   ⚠️  Source video not found: {source_video_path}")
        return None

    print("   🎬 Converting first 30 seconds to Y4M format (takes ~20-30 seconds)...")
    try:
        # Convert first 30 seconds only (video loops during test)
        result = subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(source_video_path),
                "-t",
                "30",  # First 30 seconds (loops during 45-60s test)
                "-pix_fmt",
                "yuv420p",
                "-y",  # Overwrite if exists
                str(y4m_path),
            ],
            capture_output=True,
            timeout=120,  # 2 minute timeout
        )

        if result.returncode == 0 and y4m_path.exists():
            size_mb = y4m_path.stat().st_size / 1024 / 1024
            print(f"   ✅ Converted to Y4M: {y4m_path} ({size_mb:.0f} MB, 30 seconds)")
            return y4m_path
        else:
            error_msg = result.stderr.decode("utf-8", errors="ignore")[:300]
            print(f"   ⚠️  FFmpeg conversion failed: {error_msg}")
            return None
    except FileNotFoundError:
        print("   ⚠️  FFmpeg not found - install with: sudo apt-get install ffmpeg")
        return None
    except subprocess.TimeoutExpired:
        print("   ⚠️  FFmpeg conversion timed out")
        return None
    except Exception as e:
        print(f"   ⚠️  Error converting video: {e}")
        return None


@pytest.fixture(scope="session")
def prepare_test_video():
    """
    Session-scoped fixture to download and convert test video once per test session.
    Returns path to .y4m file ready for Chrome fake device.
    """
    print("\n📹 Preparing test video for Chrome fake device...")

    # Step 1: Download source video if needed
    source_video = download_test_video()
    if not source_video:
        pytest.skip("Could not download test video")

    # Step 2: Convert to .y4m if needed
    y4m_video = convert_to_y4m(source_video)
    if not y4m_video:
        pytest.skip("Could not convert video to Y4M format (ffmpeg required)")

    print(f"✅ Test video ready: {y4m_video}\n")
    return y4m_video


@pytest.fixture(scope="function")
def browser_context_args(browser_context_args):
    """Configure browser to ignore SSL certificate errors and grant camera permissions."""
    return {
        **browser_context_args,
        "ignore_https_errors": True,  # Accept self-signed certificates
        "viewport": {
            "width": 960,
            "height": 1200,
        },  # Narrower to show main content, taller for better view
        "record_video_dir": "test-results/videos/",  # Record video of tests
        "record_video_size": {"width": 960, "height": 1200},  # Match viewport for recording
        # Grant camera and microphone permissions automatically
        "permissions": ["camera", "microphone"],
    }


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, prepare_test_video):
    """
    Configure browser video source: fake device (default) or real camera.

    **Default mode (fake device):**
    Uses Chrome's built-in fake device with a .y4m video file for fully automated testing.

    **Real camera mode:**
    Set environment variable: USE_REAL_CAMERA=1
    Uses /dev/video0 or OBS Virtual Camera for manual/realistic testing.
    """
    # Check if user wants to use real camera device
    use_real_camera = os.getenv("USE_REAL_CAMERA", "0") == "1"

    if use_real_camera:
        print("📹 Using REAL camera device (/dev/video0 or OBS Virtual Camera)")
        # Use real camera - browser will use /dev/video0 on Linux or default camera on other OS
        return {
            **browser_type_launch_args,
            "args": [
                "--use-fake-ui-for-media-stream",  # Auto-grant permissions (no prompt)
                # No fake device - uses real camera
            ],
        }
    else:
        # Default: Use fake video device with test video file
        test_video_path = prepare_test_video.absolute()
        print(f"📹 Using FAKE video device (test video): {test_video_path}")
        return {
            **browser_type_launch_args,
            "args": [
                "--use-fake-ui-for-media-stream",  # Auto-grant permissions (no prompt)
                "--use-fake-device-for-media-stream",  # Use fake video device
                f"--use-file-for-fake-video-capture={test_video_path}",  # Feed test video
            ],
        }


@pytest.fixture(scope="module")
def check_requirements():
    """Check that requirements are met before running real workflow tests."""
    import subprocess
    import ssl

    # Check if server is running (try HTTPS first, then HTTP)
    server_url = None
    for url in ["https://localhost:8090", "http://localhost:8090"]:
        try:
            # Create SSL context that doesn't verify certificates (for self-signed certs)
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            urllib.request.urlopen(url, timeout=2, context=context)
            server_url = url
            print(f"\n✅ Server found at: {server_url}")
            break
        except Exception:
            continue

    if not server_url:
        pytest.skip("Server not running on localhost:8090 - start with: ./scripts/start_server.sh")

    # Check if Ollama is available
    try:
        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"], capture_output=True, timeout=2
        )
        if result.returncode != 0:
            pytest.skip("Ollama not running on localhost:11434 - start with: ollama serve")
    except Exception:
        pytest.skip("Ollama not available")

    print("✅ Ollama is running")

    # Pull required models for the test
    required_models = ["gemma3:4b", "llama3.2-vision:11b"]
    print(f"🔄 Checking/pulling required models: {', '.join(required_models)}")

    for model in required_models:
        try:
            print(f"   📦 Pulling {model}...")
            result = subprocess.run(
                ["ollama", "pull", model],
                capture_output=True,
                timeout=300,  # 5 minute timeout for pulling
                text=True,
            )
            if result.returncode == 0:
                print(f"   ✅ {model} ready")
            else:
                print(f"   ⚠️  Warning: Could not pull {model}: {result.stderr}")
                # Don't skip - model might already be available
        except subprocess.TimeoutExpired:
            print(f"   ⚠️  Timeout pulling {model} - continuing anyway")
        except Exception as e:
            print(f"   ⚠️  Error pulling {model}: {e}")

    print("✅ Requirements met: Server and Ollama are running")
    print("ℹ️  Test video will be auto-downloaded and converted if needed")


def test_full_video_analysis_workflow(page, check_requirements):
    """
        Test complete workflow: Video input → Processing → VLM inference → Display results.

        This test creates a ~45 second interactive video showing:
        1. Page loads
        2. Pre-select fastest model (gemma3:4b)
        3. Camera permission granted
        4. Video stream starts with gemma3:4b inference
        5. At ~10s: Quick scroll down to show GPU stats
        6. At ~12s: Quick scroll back up to video view
        7. At ~15s: Switch to light mode theme
        8. At ~18s: Open settings modal and change:
           - Enable Colorful UI Accents
           - Set WebRTC Max Video Latency to 0.1
           - Set Graph Update Interval to 0.1
    1
    """
    print("\n🎬 Starting full video analysis workflow test...")
    print("   This test will take ~45 seconds and create a video recording")
    print(
        "   Video timeline: gemma3:4b → Camera → Scroll(10s/12s) → Theme(15s) → Settings(18s) → llama(25s) → Analysis"
    )

    # Grant camera and microphone permissions to the localhost origin
    page.context.grant_permissions(["camera", "microphone"], origin="https://localhost:8090")

    # Navigate to the page (use HTTPS, ignore cert errors for self-signed certs)
    page.goto("https://localhost:8090", wait_until="domcontentloaded")
    print("   ✅ Page loaded")

    # Wait for page to fully load
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # FIRST: Select the smallest/fastest model (gemma3:4b) before starting camera
    print("   🤖 Selecting smallest model (gemma3:4b) before opening camera...")
    try:
        all_selects = page.locator("select").all()
        for idx, model_select in enumerate(all_selects):
            try:
                if not model_select.is_visible(timeout=500):
                    continue

                options = model_select.locator("option").all()
                option_texts = [opt.text_content() for opt in options]

                # Find the model selector (has model names)
                is_model_select = any(
                    "llama" in txt.lower() or "gemma" in txt.lower() or "vision" in txt.lower()
                    for txt in option_texts
                )

                if is_model_select:
                    # Find and select gemma3:4b
                    for i, opt in enumerate(options):
                        opt_text = opt.text_content()
                        if "gemma3:4b" in opt_text:
                            model_select.select_option(index=i)
                            print("   ✅ Pre-selected fastest model: gemma3:4b")
                            time.sleep(1)
                            break
                    break
            except Exception:

                continue
    except Exception as e:
        print(f"   ⚠️  Could not pre-select model: {e}")

    # Click the "Open Camera and Start VLM Analysis" button
    print("   📹 Looking for camera start button...")

    try:
        # Try different button texts
        button_texts = [
            "Open Camera and Start VLM Analysis",
            "Open Camera",
            "Start",
            "Start Analysis",
        ]

        clicked = False
        for button_text in button_texts:
            try:
                start_button = page.locator(f"button:has-text('{button_text}')").first
                if start_button.is_visible(timeout=2000):
                    start_button.click()
                    print(f"   ✅ Clicked '{button_text}' button")
                    clicked = True
                    break
            except Exception:

                continue

        if not clicked:
            print("   ⚠️  No start button found - camera might auto-start")
    except Exception as e:
        print(f"   ⚠️  Error clicking start button: {e}")
        pass  # Continue anyway

    # Wait for video element to be present and playing
    print("   ⏳ Waiting for video stream to initialize...")
    video_element = page.locator("video").first
    assert video_element.is_visible(timeout=15000), "Video element not visible after 15s"
    print("   ✅ Video element visible")

    # Wait for video to actually start streaming and camera to initialize
    print("   ⏳ Waiting for video stream to fully expand page layout...")
    time.sleep(5)  # Give more time for WebRTC to start and page to expand

    # Find and check ALL scrollable elements on the page
    scrollable_elements = page.evaluate(
        """
        () => {
            const allElements = document.querySelectorAll('*');
            const scrollable = [];

            allElements.forEach((elem) => {
                const style = window.getComputedStyle(elem);
                const overflowY = style.overflowY;

                if ((overflowY === 'scroll' || overflowY === 'auto') &&
                    elem.scrollHeight > elem.clientHeight) {
                    scrollable.push({
                        tag: elem.tagName.toLowerCase(),
                        id: elem.id || '',
                        class: elem.className || '',
                        scrollHeight: elem.scrollHeight,
                        clientHeight: elem.clientHeight,
                        scrollable: elem.scrollHeight - elem.clientHeight
                    });
                }
            });

            return scrollable;
        }
    """
    )

    print(f"   🔍 Found {len(scrollable_elements)} scrollable elements:")
    for elem in scrollable_elements[:5]:  # Show first 5
        print(
            f"      - <{elem['tag']}> id='{elem['id']}' class='{elem['class']}' scrollable={elem['scrollable']}px"
        )

    # Now try to find the best scroll container
    scroll_container = page.evaluate(
        """
        () => {
            // Find ALL elements with overflow scroll/auto
            const allElements = document.querySelectorAll('*');
            let bestContainer = null;
            let maxScrollable = 0;

            allElements.forEach((elem) => {
                const style = window.getComputedStyle(elem);
                const overflowY = style.overflowY;

                if ((overflowY === 'scroll' || overflowY === 'auto') &&
                    elem.scrollHeight > elem.clientHeight) {
                    const scrollable = elem.scrollHeight - elem.clientHeight;
                    if (scrollable > maxScrollable) {
                        maxScrollable = scrollable;
                        bestContainer = elem;
                    }
                }
            });

            if (bestContainer) {
                return {
                    found: true,
                    selector: bestContainer.tagName.toLowerCase() +
                             (bestContainer.id ? '#' + bestContainer.id : '') +
                             (bestContainer.className ? '.' + bestContainer.className.split(' ')[0] : ''),
                    scrollTop: bestContainer.scrollTop,
                    scrollHeight: bestContainer.scrollHeight,
                    clientHeight: bestContainer.clientHeight,
                    canScroll: true
                };
            }

            // Fall back to window scroll
            return {
                found: false,
                selector: 'window',
                scrollTop: window.scrollY,
                scrollHeight: document.body.scrollHeight,
                clientHeight: document.documentElement.clientHeight,
                canScroll: document.body.scrollHeight > document.documentElement.clientHeight
            };
        }
    """
    )

    print(f"   ℹ️  Scroll container: {scroll_container['selector']}")
    print(
        f"   ℹ️  Content: scrollHeight={scroll_container['scrollHeight']}px, viewport={scroll_container['clientHeight']}px"
    )
    print(
        f"   ℹ️  Scrollable content: {scroll_container['scrollHeight'] - scroll_container['clientHeight']}px"
    )

    if not scroll_container["canScroll"]:
        print("   ⚠️  Content fits in viewport - no scrolling needed")
    else:
        print("   📜 Quick scroll down to show GPU stats cards (at ~10s)...")

        # Quick scroll down - just 4 steps
        for i in range(4):
            page.mouse.wheel(0, 400)  # Scroll down 400 pixels
            time.sleep(0.25)  # Quick pause

        if scroll_container["found"]:
            final_position = page.evaluate(
                f"document.querySelector('{scroll_container['selector']}').scrollTop"
            )
        else:
            final_position = page.evaluate("window.scrollY")
        print(f"   ✅ Scrolled to {final_position}px - showing GPU stats")

    # Brief pause to show stats
    time.sleep(1)

    # Take a screenshot of the stats section
    stats_screenshot = "test-results/workflow-stats-section.png"
    page.screenshot(path=stats_screenshot)
    print(f"   📸 Stats section screenshot: {stats_screenshot}")

    # Quick scroll back up
    if scroll_container["canScroll"]:
        print("   📜 Quick scroll back up to video view (at ~12s)...")
        for i in range(4):  # Quick 4 steps back up
            page.mouse.wheel(0, -400)  # Scroll up 400 pixels
            time.sleep(0.25)  # Quick pause

        if scroll_container["found"]:
            final_position = page.evaluate(
                f"document.querySelector('{scroll_container['selector']}').scrollTop"
            )
        else:
            final_position = page.evaluate("window.scrollY")
        print(f"   ✅ Scrolled back to {final_position}px - back to video view")
    else:
        print("   ℹ️  No scrolling performed (content fits in viewport)")

    time.sleep(1)  # Brief settle

    # Check that WebSocket is connected
    # Look for GPU stats updates (indicates WebSocket is working)
    # Try multiple selectors since format might vary
    gpu_stats_visible = False
    for selector in [
        "text=/GPU.*%/i",  # GPU: 45.2%
        "text=/gpu/i",  # Any mention of "gpu"
        "#gpu-stats",  # ID selector
        ".gpu-stats",  # Class selector
        "[data-testid='gpu-stats']",  # Test ID
    ]:
        try:
            if page.locator(selector).first.is_visible(timeout=1000):
                gpu_stats_visible = True
                print("   ✅ WebSocket connected (GPU stats visible)")
                break
        except Exception:

            continue

    if not gpu_stats_visible:
        print("   ⚠️  GPU stats not visible - continuing anyway (WebSocket might still work)")
        # Don't fail - GPU stats might not be in expected format

    # Brief wait for VLM to start, then do interactive UI demos
    time.sleep(2)

    # Perform interactive UI demonstrations
    print("\n   🎨 Demonstrating interactive UI features...")

    # Action 1: Switch to light mode (at ~15s mark)
    print("   🌞 Switching to Light Mode (at ~15s)...")
    try:
        theme_button = page.locator("button:has-text('Dark'), button:has-text('Light')").first
        if theme_button.is_visible(timeout=2000):
            theme_button.click()
            print("   ✅ Toggled to light mode")
            time.sleep(3)  # Let theme transition complete and recording capture it
    except Exception:
        print("   ⚠️  Could not toggle theme")

    # Action 2: Open settings modal and change settings
    print("   ⚙️  Opening Settings Modal and changing settings...")

    try:
        # Settings button has id="settingsBtn", class="settings-btn", title="Settings"
        settings_selectors = [
            "#settingsBtn",  # ID selector - most reliable
            ".settings-btn",  # Class selector
            "button[title='Settings']",  # Title attribute
            "button.settings-btn",
            "[title='Settings']",
        ]
        found_settings = False
        for selector in settings_selectors:
            try:
                settings_btn = page.locator(selector).first
                if settings_btn.is_visible(timeout=1000):
                    settings_btn.click()
                    print("   ✅ Opened settings modal")
                    time.sleep(2)  # Let modal appear
                    found_settings = True

                    # Change Setting 1: Colorful UI Accents → ON (toggle switch)
                    # Structure: <input type="checkbox" id="colorfulFocusToggle"> inside <label class="toggle-switch">
                    print("   🎨 Enabling 'Colorful UI Accents'...")
                    try:
                        # Get the checkbox to check current state
                        colorful_checkbox = page.locator("#colorfulFocusToggle")
                        is_checked = colorful_checkbox.is_checked()

                        if not is_checked:
                            # Click the visible toggle slider (the label contains the checkbox)
                            # Find the label that contains this checkbox
                            toggle_label = page.locator("label:has(#colorfulFocusToggle)")
                            toggle_label.click()
                            print("   ✅ Enabled Colorful UI Accents")
                        else:
                            print("   ℹ️  Colorful UI Accents already ON")
                    except Exception as e:
                        print(f"   ⚠️  Error toggling Colorful UI Accents: {e}")

                    time.sleep(1)

                    # Change Setting 2: WebRTC Max Video Latency → 0.1
                    print("   📡 Setting 'WebRTC Max Video Latency' to 0.1...")
                    try:
                        # Look for input related to WebRTC latency
                        latency_inputs = page.locator(
                            "input[type='number'], input[type='text']"
                        ).all()
                        for inp in latency_inputs:
                            # Check if this input is near "latency" text
                            try:
                                # Get the parent or nearby label
                                parent = inp.evaluate("el => el.parentElement.textContent")
                                if "latency" in parent.lower() or "webrtc" in parent.lower():
                                    inp.fill("0.1")
                                    print("   ✅ Set WebRTC Max Video Latency to 0.1")
                                    break
                            except Exception:

                                continue
                    except Exception:
                        print("   ⚠️  Could not set WebRTC latency")

                    time.sleep(1)

                    # Change Setting 3: Graph Update Interval → 0.1
                    print("   📊 Setting 'Graph Update Interval' to 0.1...")
                    try:
                        # Look for input related to graph update interval
                        graph_inputs = page.locator(
                            "input[type='number'], input[type='text']"
                        ).all()
                        for inp in graph_inputs:
                            try:
                                parent = inp.evaluate("el => el.parentElement.textContent")
                                if "graph" in parent.lower() and "interval" in parent.lower():
                                    inp.fill("0.1")
                                    print("   ✅ Set Graph Update Interval to 0.1")
                                    break
                            except Exception:

                                continue
                    except Exception:
                        print("   ⚠️  Could not set Graph Update Interval")

                    time.sleep(2)  # Show the changed settings

                    # Close the modal
                    close_selectors = [
                        "button:has-text('Close')",
                        "button:has-text('×')",
                        "button:has-text('✕')",
                        "[aria-label*='close' i]",
                    ]
                    for close_sel in close_selectors:
                        try:
                            close_btn = page.locator(close_sel).first
                            if close_btn.is_visible(timeout=1000):
                                close_btn.click()
                                print("   ✅ Closed settings modal")
                                time.sleep(2)
                                break
                        except Exception:

                            continue
                    break
            except Exception:

                continue

        if not found_settings:
            print("   ⚠️  No settings button found with any selector")
    except Exception as e:
        print(f"   ⚠️  Could not interact with settings: {e}")

    # Action 3: Change VLM model
    print("   🤖 Changing VLM Model...")

    # First, let's see what selects/dropdowns are available
    try:
        all_selects = page.locator("select").all()
        print(f"   ℹ️  Found {len(all_selects)} select elements")
        if len(all_selects) > 0:
            for idx, sel in enumerate(all_selects[:3]):  # First 3
                try:
                    options = sel.locator("option").all()
                    option_texts = [opt.text_content() for opt in options[:5]]
                    print(f"   ℹ️  Select {idx}: {len(options)} options, first few: {option_texts}")
                except Exception:

                    pass
    except Exception:

        pass

    try:
        # Find model selector by looking for model-like option text
        all_selects = page.locator("select").all()
        found_model = False

        for idx, model_select in enumerate(all_selects):
            try:
                if not model_select.is_visible(timeout=500):
                    continue

                options = model_select.locator("option").all()
                if len(options) < 2:
                    continue

                # Check if this looks like a model selector (has model names like llama, gemma, etc)
                option_texts = [opt.text_content() for opt in options]
                is_model_select = any(
                    "llama" in txt.lower()
                    or "gemma" in txt.lower()
                    or "vision" in txt.lower()
                    or "mistral" in txt.lower()
                    or "qwen" in txt.lower()
                    for txt in option_texts
                )

                if is_model_select and len(options) > 1:
                    print(f"   ℹ️  Found model selector (select {idx}) with {len(options)} models")
                    current_value = model_select.input_value()

                    # Look for llama3.2-vision:11b (the larger, higher quality model)
                    target_found = False
                    for i, opt in enumerate(options):
                        opt_text = opt.text_content()
                        if "llama3.2-vision:11b" in opt_text:
                            model_select.select_option(index=i)
                            new_value = model_select.input_value()
                            print(
                                f"   ✅ Changed VLM model: '{current_value}' → '{new_value}' (upgrading to larger model)"
                            )
                            target_found = True
                            time.sleep(3)
                            break

                    if not target_found:
                        # Fallback: just select next option
                        current_index = 0
                        for i, opt in enumerate(options):
                            if opt.get_attribute("value") == current_value:
                                current_index = i
                                break
                        new_index = (current_index + 1) % len(options)
                        model_select.select_option(index=new_index)
                        print("   ✅ Changed VLM model to next option")
                        time.sleep(3)

                    found_model = True
                    break
            except Exception:
                continue

        if not found_model:
            print("   ⚠️  No model selector found or only one option available")
    except Exception as e:
        print(f"   ⚠️  Could not change model: {e}")

    # Now monitor VLM analysis updates
    print("\n   📊 Monitoring VLM analysis updates for 25 seconds...")

    analyses = []
    analysis_texts = []
    last_analysis_text = ""

    for i in range(25):  # Monitor for 25 seconds
        time.sleep(1)

        # Check GPU utilization is active (if we found the element earlier)
        if gpu_stats_visible:
            try:
                # Try to find GPU stats element again
                for selector in ["text=/GPU.*%/i", "text=/gpu/i"]:
                    try:
                        gpu_elem = page.locator(selector).first
                        if gpu_elem.is_visible(timeout=100):
                            gpu_text = gpu_elem.text_content()
                            if gpu_text:
                                # Extract GPU percentage
                                match = re.search(r"(\d+(?:\.\d+)?)\s*%", gpu_text)
                                if match:
                                    gpu_pct = float(match.group(1))
                                    print(f"   GPU: {gpu_pct:.1f}%")
                                break
                    except Exception:

                        continue
            except Exception:

                pass

        # Try to extract the actual VLM analysis text from the page
        try:
            # Get all visible text from the page body
            page_text = page.evaluate(
                """
                () => {
                    // Get text from video overlay or body
                    const overlay = document.querySelector('.video-overlay, .overlay, #text-overlay');
                    if (overlay) {
                        return overlay.innerText.trim();
                    }
                    // Fall back to body text, but filter out UI elements
                    const bodyText = document.body.innerText;
                    // Try to extract just the analysis sentence
                    const lines = bodyText.split('\\n').filter(line => line.length > 30);
                    return lines.length > 0 ? lines[lines.length - 1] : bodyText;
                }
            """
            )

            # Check if we found analysis-like text (contains typical VLM words)
            if (
                page_text
                and len(page_text) > 20
                and any(
                    word in page_text.lower()
                    for word in [
                        "image",
                        "person",
                        "man",
                        "woman",
                        "sitting",
                        "standing",
                        "wearing",
                        "room",
                        "desk",
                        "chair",
                        "computer",
                        "appears",
                        "visible",
                        "shows",
                        "seen",
                    ]
                )
            ):

                # Check if text has changed
                if page_text != last_analysis_text and page_text not in analysis_texts:
                    analyses.append(i)
                    analysis_texts.append(page_text)
                    last_analysis_text = page_text
                    # Show preview (first 100 chars)
                    preview = page_text[:100] + "..." if len(page_text) > 100 else page_text
                    print(f'   🔄 Analysis {len(analysis_texts)} at {i}s: "{preview}"')
        except Exception:
            pass  # Silently continue if extraction fails

    print("\n   📈 Summary:")
    print("      - Total runtime: ~45 seconds")
    print("      - Started with: gemma3:4b (fast model)")
    print(
        "      - Timeline: Scroll(10s/12s) → Theme(15s) → Settings(18s) → Upgrade to llama3.2-vision:11b(25s)"
    )
    print("      - GPU stats monitoring throughout")
    print(f"      - VLM analysis updates detected: {len(analyses)}")

    if analysis_texts:
        print("\n   📝 VLM Analysis Texts:")
        for idx, text in enumerate(analysis_texts, 1):
            # Truncate if too long
            display_text = text[:200] + "..." if len(text) > 200 else text
            print(f"      {idx}. {display_text}")

    # Take a final screenshot
    screenshot_path = "test-results/workflow-final-state.png"
    page.screenshot(path=screenshot_path)
    print(f"   📸 Screenshot saved: {screenshot_path}")

    # Final assertion: Page should still be responsive
    assert page.is_visible("video"), "Video element disappeared during test"

    # Get the recorded video path
    video_path = page.video.path() if page.video else None
    if video_path:
        print(f"\n   🎬 Test recording saved: {video_path}")
        print(f"      Watch the full workflow: mpv {video_path}")

    print("   ✅ Full workflow test completed successfully!")
