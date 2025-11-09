"""
WebRTC Live VLM WebUI Server
Main server that handles WebRTC connections and serves the web interface
"""

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import aiohttp
from aiohttp import web
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer,
)
from aiortc.contrib.media import MediaRelay

from .vlm_service import VLMService
from .video_processor import VideoProcessorTrack
from .gpu_monitor import create_monitor

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global objects
relay = MediaRelay()
pcs = set()
vlm_service = None
websockets = set()  # Track active WebSocket connections
gpu_monitor = None  # GPU monitoring instance
gpu_monitor_task = None  # Background task for GPU monitoring


def is_port_available(port, host="0.0.0.0"):
    """Check if a port is available for binding"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.close()
        return True
    except OSError:
        return False


def find_process_using_port(port):
    """Find what process is using a port (Linux/Unix only)"""
    try:
        # Try lsof first (more reliable)
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-t"], capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = result.stdout.strip().split()[0]
            # Get process name
            name_result = subprocess.run(
                ["ps", "-p", pid, "-o", "comm="], capture_output=True, text=True, timeout=2
            )
            if name_result.returncode == 0:
                return f"PID {pid} ({name_result.stdout.strip()})"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # lsof not available, try netstat
        try:
            result = subprocess.run(
                ["netstat", "-tulpn"], capture_output=True, text=True, timeout=2
            )
            for line in result.stdout.split("\n"):
                if f":{port}" in line and "LISTEN" in line:
                    parts = line.split()
                    if len(parts) >= 7:
                        return parts[-1]  # PID/Program name
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return "unknown process"


def find_available_port(start_port=8080, max_attempts=10):
    """Find next available port starting from start_port"""
    for port in range(start_port, start_port + max_attempts):
        if is_port_available(port):
            return port
    return None


async def detect_local_service_and_model():
    """
    Auto-detect available local VLM services and select a model
    Returns: (api_base, model_name) or (None, None) if no service found
    """
    services = [
        ("http://localhost:11434/v1", "Ollama"),
        ("http://localhost:8000/v1", "vLLM"),
        ("http://localhost:30000/v1", "SGLang"),
    ]

    for api_base, service_name in services:
        try:
            # Try to connect to the service
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                async with session.get(f"{api_base}/models") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = data.get("data", [])
                        if models:
                            # Prefer vision models
                            vision_keywords = ["vision", "llava", "llama-3.2", "gemini"]
                            for model in models:
                                model_id = model.get("id", "")
                                if any(keyword in model_id.lower() for keyword in vision_keywords):
                                    logger.info(f"✅ Auto-detected {service_name} at {api_base}")
                                    logger.info(f"   Selected model: {model_id}")
                                    return (api_base, model_id)

                            # If no vision model found, use the first one
                            model_id = models[0].get("id", "")
                            logger.info(f"✅ Auto-detected {service_name} at {api_base}")
                            logger.info(
                                f"   Selected model: {model_id} (vision model preferred but not found)"
                            )
                            return (api_base, model_id)
        except Exception as e:
            logger.debug(f"Service {service_name} not available at {api_base}: {e}")
            continue

    return (None, None)


async def index(request):
    """Serve the main HTML page"""
    content = open(os.path.join(os.path.dirname(__file__), "static", "index.html"), "r").read()
    return web.Response(content_type="text/html", text=content)


async def models(request):
    """Return available models from the VLM API"""
    try:
        # Check if custom API base and key are provided in query params
        api_base = request.rel_url.query.get("api_base")
        api_key = request.rel_url.query.get("api_key")

        if api_base:
            # Query models from the provided API endpoint
            from openai import AsyncOpenAI

            temp_client = AsyncOpenAI(base_url=api_base, api_key=api_key if api_key else "EMPTY")
            models_response = await temp_client.models.list()
            models_list = [
                {"id": model.id, "name": model.id, "current": False}
                for model in models_response.data
            ]
            return web.Response(
                content_type="application/json", text=json.dumps({"models": models_list})
            )
        elif vlm_service:
            # Use the server's VLM service
            models_response = await vlm_service.client.models.list()
            models_list = [
                {"id": model.id, "name": model.id, "current": model.id == vlm_service.model}
                for model in models_response.data
            ]
            return web.Response(
                content_type="application/json", text=json.dumps({"models": models_list})
            )
        else:
            return web.Response(
                content_type="application/json",
                text=json.dumps({"models": [], "error": "VLM service not initialized"}),
            )
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        # Return current model as fallback
        if vlm_service:
            return web.Response(
                content_type="application/json",
                text=json.dumps(
                    {
                        "models": [
                            {"id": vlm_service.model, "name": vlm_service.model, "current": True}
                        ]
                    }
                ),
            )
        return web.Response(
            content_type="application/json", text=json.dumps({"models": [], "error": str(e)})
        )


async def detect_services(request):
    """Detect available local VLM services"""
    services = [
        {"name": "Ollama", "url": "http://localhost:11434/v1", "port": 11434, "path": "/api/tags"},
        {"name": "vLLM", "url": "http://localhost:8000/v1", "port": 8000, "path": "/v1/models"},
        {"name": "SGLang", "url": "http://localhost:30000/v1", "port": 30000, "path": "/v1/models"},
    ]

    detected = []

    async def check_service(service):
        """Check if a service is running by probing its endpoint"""
        try:
            timeout = aiohttp.ClientTimeout(total=1.0)  # 1 second timeout
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"http://localhost:{service['port']}{service['path']}"
                async with session.get(url) as response:
                    if response.status in [200, 404]:  # 404 is ok, means server is running
                        logger.info(f"Detected {service['name']} at {service['url']}")
                        return service
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        return None

    # Check all services concurrently
    results = await asyncio.gather(*[check_service(s) for s in services])
    detected = [s for s in results if s is not None]

    # Default to NVIDIA API Catalog if no local services found
    if not detected:
        detected.append(
            {
                "name": "NVIDIA API Catalog",
                "url": "https://integrate.api.nvidia.com/v1",
                "port": None,
                "path": None,
                "requires_key": True,
            }
        )

    return web.Response(
        content_type="application/json",
        text=json.dumps({"detected": detected, "default": detected[0] if detected else None}),
    )


async def websocket_handler(request):
    """Handle WebSocket connections for text updates"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    websockets.add(ws)
    logger.info(f"WebSocket client connected. Total clients: {len(websockets)}")

    try:
        # Send initial message with current server configuration
        await ws.send_json({"type": "status", "text": "Connected to server", "status": "Ready"})

        # Send current server configuration
        if vlm_service:
            await ws.send_json(
                {
                    "type": "server_config",
                    "model": vlm_service.model,
                    "api_base": vlm_service.api_base,
                    "prompt": vlm_service.prompt,
                }
            )

        # Keep connection alive and handle incoming messages
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)

                    if data.get("type") == "update_prompt":
                        new_prompt = data.get("prompt", "").strip()
                        max_tokens = data.get("max_tokens")
                        if new_prompt and vlm_service:
                            vlm_service.update_prompt(new_prompt, max_tokens)
                            logger.info(f"Prompt updated: {new_prompt}, max_tokens: {max_tokens}")

                            # Confirm to client
                            await ws.send_json(
                                {
                                    "type": "prompt_updated",
                                    "prompt": new_prompt,
                                    "max_tokens": max_tokens,
                                }
                            )

                    elif data.get("type") == "update_model":
                        new_model = data.get("model", "").strip()
                        api_base = data.get("api_base", "").strip()
                        api_key = data.get("api_key", "").strip()

                        if new_model and vlm_service:
                            # Update model
                            vlm_service.model = new_model

                            # Update API settings if provided
                            # User may have switched to different service (Ollama ↔ vLLM)
                            if api_base:
                                vlm_service.update_api_settings(
                                    api_base, api_key if api_key else None
                                )
                                logger.info(f"Model updated: {new_model}, API: {api_base}")
                            else:
                                logger.info(f"Model updated: {new_model}")

                            # Confirm to client
                            await ws.send_json(
                                {
                                    "type": "model_updated",
                                    "model": new_model,
                                    "api_base": vlm_service.api_base,
                                }
                            )

                    elif data.get("type") == "update_processing":
                        process_every = data.get("process_every", 30)
                        try:
                            process_every = int(process_every)
                            if 1 <= process_every <= 3600:  # Up to 3600 frames (2 minutes @ 30fps)
                                from .video_processor import VideoProcessorTrack

                                old_value = VideoProcessorTrack.process_every_n_frames
                                VideoProcessorTrack.process_every_n_frames = process_every
                                logger.info(
                                    f"Processing interval updated: {old_value} → {process_every} frames"
                                )

                                # Confirm to client
                                await ws.send_json(
                                    {"type": "processing_updated", "process_every": process_every}
                                )
                            else:
                                logger.warning(
                                    f"Processing interval out of range (1-3600): {process_every}"
                                )
                        except ValueError:
                            logger.error(f"Invalid processing interval: {process_every}")

                    elif data.get("type") == "update_max_latency":
                        max_latency = data.get("max_latency", 0.0)
                        try:
                            max_latency = float(max_latency)
                            if 0 <= max_latency <= 10.0:
                                from .video_processor import VideoProcessorTrack

                                old_value = VideoProcessorTrack.max_frame_latency
                                VideoProcessorTrack.max_frame_latency = max_latency
                                status = "disabled" if max_latency == 0 else f"{max_latency:.1f}s"
                                old_status = "disabled" if old_value == 0 else f"{old_value:.1f}s"
                                logger.info(f"Max frame latency updated: {old_status} → {status}")

                                # Confirm to client
                                await ws.send_json(
                                    {"type": "max_latency_updated", "max_latency": max_latency}
                                )
                            else:
                                logger.warning(f"Max latency out of range (0-10.0): {max_latency}")
                        except ValueError:
                            logger.error(f"Invalid max latency value: {max_latency}")
                except json.JSONDecodeError:
                    logger.error("Invalid JSON from client")
                except Exception as e:
                    logger.error(f"Error handling client message: {e}")
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")
    finally:
        websockets.discard(ws)
        logger.info(f"WebSocket client disconnected. Total clients: {len(websockets)}")

    return ws


def broadcast_text_update(text: str, metrics: dict):
    """Broadcast text update and metrics to all connected WebSocket clients"""
    if not websockets:
        return

    message = json.dumps({"type": "vlm_response", "text": text, "metrics": metrics})

    # Send to all connected clients
    dead_websockets = set()
    for ws in websockets:
        try:
            # Use asyncio to send without blocking
            asyncio.create_task(ws.send_str(message))
        except Exception as e:
            logger.error(f"Error sending to websocket: {e}")
            dead_websockets.add(ws)

    # Clean up dead connections
    websockets.difference_update(dead_websockets)


def broadcast_gpu_stats(stats: dict):
    """Broadcast GPU stats to all connected WebSocket clients"""
    if not websockets:
        return

    message = json.dumps({"type": "gpu_stats", "stats": stats})

    # Send to all connected clients
    dead_websockets = set()
    for ws in websockets:
        try:
            asyncio.create_task(ws.send_str(message))
        except Exception as e:
            logger.error(f"Error sending GPU stats to websocket: {e}")
            dead_websockets.add(ws)

    # Clean up dead connections
    websockets.difference_update(dead_websockets)


async def gpu_monitor_loop():
    """Background task to periodically collect and broadcast GPU stats"""
    global gpu_monitor

    if not gpu_monitor:
        logger.warning("GPU monitor not initialized, skipping monitoring")
        return

    logger.info("GPU monitoring loop started")

    try:
        while True:
            # Get current stats
            stats = gpu_monitor.get_stats()

            # Update history with current stats
            gpu_monitor.update_history(stats)

            # Add history to stats
            stats["history"] = gpu_monitor.get_history()

            # Broadcast to all connected clients
            broadcast_gpu_stats(stats)

            # Update every 0.25 seconds for detailed GPU monitoring
            await asyncio.sleep(0.25)
    except asyncio.CancelledError:
        logger.info("GPU monitoring loop cancelled")
    except Exception as e:
        logger.error(f"Error in GPU monitoring loop: {e}")


async def offer(request):
    """Handle WebRTC offer from client"""
    params = await request.json()
    offer_sdp = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    # Create RTCPeerConnection with STUN servers for Docker/NAT compatibility
    config = RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
        ]
    )
    pc = RTCPeerConnection(configuration=config)
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"Connection state: {pc.connectionState}")
        if pc.connectionState in ["failed", "closed"]:
            await pc.close()
            pcs.discard(pc)

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logger.info(f"ICE connection state: {pc.iceConnectionState}")
        if pc.iceConnectionState == "failed":
            logger.error("ICE connection failed - check firewall/NAT settings")

    @pc.on("icegatheringstatechange")
    async def on_icegatheringstatechange():
        logger.info(f"ICE gathering state: {pc.iceGatheringState}")

    @pc.on("track")
    def on_track(track):
        logger.info(f"Received track: {track.kind}")

        if track.kind == "video":
            # Create processor track with VLM service and text callback
            processor_track = VideoProcessorTrack(
                relay.subscribe(track), vlm_service, text_callback=broadcast_text_update
            )

            # Add processed track back to connection
            pc.addTrack(processor_track)
            logger.info("Added processed video track back to peer connection")

        @track.on("ended")
        async def on_ended():
            logger.info(f"Track {track.kind} ended")

    # Handle offer - this will trigger on_track
    await pc.setRemoteDescription(offer_sdp)

    # Create answer - this must happen after tracks are added
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    logger.info(f"Created answer with {len(pc.getTransceivers())} transceivers")

    return web.Response(
        content_type="application/json",
        text=json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}),
    )


async def on_startup(app):
    """Initialize resources on server startup"""
    global gpu_monitor, gpu_monitor_task

    # Initialize GPU monitor
    try:
        gpu_monitor = create_monitor()
        logger.info("GPU monitor initialized")
    except Exception as e:
        logger.error(f"Failed to initialize GPU monitor: {e}")
        gpu_monitor = None

    # Start GPU monitoring background task
    if gpu_monitor:
        gpu_monitor_task = asyncio.create_task(gpu_monitor_loop())
        logger.info("GPU monitoring task started")


async def on_shutdown(app):
    """Cleanup on server shutdown"""
    global gpu_monitor, gpu_monitor_task

    logger.info("Shutting down server...")

    # Stop GPU monitoring task
    if gpu_monitor_task:
        gpu_monitor_task.cancel()
        try:
            await gpu_monitor_task
        except asyncio.CancelledError:
            pass
        logger.info("GPU monitoring task stopped")

    # Cleanup GPU monitor
    if gpu_monitor:
        gpu_monitor.cleanup()
        logger.info("GPU monitor cleaned up")

    # Close all websockets
    for ws in list(websockets):
        await ws.close()
    websockets.clear()

    # Close all peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()

    logger.info("Cleanup complete")


async def create_app(test_mode=False):
    """
    Create and configure the aiohttp web application.

    Args:
        test_mode: If True, skip GPU monitoring and use test configuration

    Returns:
        Configured web.Application instance
    """
    # Create web application
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/models", models)
    app.router.add_get("/detect-services", detect_services)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_post("/offer", offer)

    # Serve static files (images, etc.)
    if test_mode:
        # In test mode, serve from src/live_vlm_webui/static/images
        images_dir = os.path.join(os.path.dirname(__file__), "static", "images")
    else:
        # In production, images directory is at project root
        images_dir = os.path.join(os.path.dirname(__file__), "..", "..", "images")

    images_dir = os.path.abspath(images_dir)
    if os.path.exists(images_dir):
        app.router.add_static("/images", images_dir, name="images")
        logger.info(f"Serving static files from: {images_dir}")

    if not test_mode:
        app.on_startup.append(on_startup)
        app.on_shutdown.append(on_shutdown)

    return app


def main():
    """Main entry point"""
    import argparse
    import ssl

    parser = argparse.ArgumentParser(
        description="WebRTC Live VLM WebUI - Real-time vision model interaction",
        epilog="Examples:\n"
        "  vLLM:    python server.py --model llama-3.2-11b-vision-instruct --api-base http://localhost:8000/v1\n"
        "  SGLang:  python server.py --model llama-3.2-11b-vision-instruct --api-base http://localhost:30000/v1\n"
        "  Ollama:  python server.py --model llava:7b --api-base http://localhost:11434/v1\n"
        "  HTTPS:   python server.py --model llava:7b --api-base http://localhost:11434/v1 --ssl-cert cert.pem --ssl-key key.pem",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8090, help="Port to bind to (default: 8090)")
    parser.add_argument(
        "--auto-port",
        action="store_true",
        help="Automatically find available port if default is taken",
    )
    parser.add_argument(
        "--model", help="VLM model name (optional, will auto-detect if not specified)"
    )
    parser.add_argument(
        "--api-base", help="VLM API base URL (optional, will auto-detect or use NVIDIA NGC)"
    )
    parser.add_argument(
        "--api-key",
        default="EMPTY",
        help="API key - use 'EMPTY' for local servers, required for NVIDIA NGC/OpenAI (default: EMPTY)",
    )
    parser.add_argument(
        "--prompt",
        default="Describe what you see in this image in one sentence.",
        help="Prompt to send to VLM (default: 'Describe what you see...')",
    )
    parser.add_argument("--process-every", type=int, default=30, help="Process every Nth frame")
    parser.add_argument("--ssl-cert", help="Path to SSL certificate file (enables HTTPS)")
    parser.add_argument("--ssl-key", help="Path to SSL private key file (enables HTTPS)")

    args = parser.parse_args()

    # Auto-detect service and model if not specified
    api_base = args.api_base
    model = args.model
    api_key = args.api_key

    if not model or not api_base:
        logger.info("No model/API specified, auto-detecting local services...")
        detected_api_base, detected_model = asyncio.run(detect_local_service_and_model())

        if detected_api_base and detected_model:
            if not api_base:
                api_base = detected_api_base
            if not model:
                model = detected_model
        else:
            # Fall back to NVIDIA NGC
            logger.warning("⚠️  No local VLM service found (Ollama, vLLM, SGLang)")
            logger.info("📡 Falling back to NVIDIA API Catalog")
            logger.info("   You'll need an API key from: https://build.nvidia.com")
            if not api_base:
                api_base = "https://integrate.api.nvidia.com/v1"
            if not model:
                model = "meta/llama-3.2-11b-vision-instruct"
            if api_key == "EMPTY":
                logger.warning("⚠️  API key required for NVIDIA API Catalog")
                logger.warning("   Set with: --api-key YOUR_API_KEY")
                logger.warning("   Or use WebUI to configure API settings after starting")

    # Initialize VLM service
    global vlm_service
    vlm_service = VLMService(model=model, api_base=api_base, api_key=api_key, prompt=args.prompt)

    # Log initialization with better formatting
    service_name = "Local" if "localhost" in api_base or "127.0.0.1" in api_base else "Cloud"
    logger.info("Initialized VLM service:")
    logger.info(f"  Model: {model}")
    logger.info(f"  API: {api_base} ({service_name})")
    logger.info(f"  Prompt: {args.prompt}")

    # Update frame processing rate in VideoProcessorTrack if needed
    # (This is a bit hacky but works for this demo)
    VideoProcessorTrack.process_every_n_frames = args.process_every

    # Create web application using create_app
    app = asyncio.run(create_app(test_mode=False))

    # Setup SSL if certificates provided
    ssl_context = None
    protocol = "http"
    if args.ssl_cert and args.ssl_key:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(args.ssl_cert, args.ssl_key)
        protocol = "https"
        logger.info("SSL enabled - using HTTPS")
    else:
        logger.warning("⚠️  SSL not enabled - webcam access requires HTTPS!")
        logger.warning("⚠️  Generate certificates with: ./generate_cert.sh")
        logger.warning("⚠️  Then restart with: --ssl-cert cert.pem --ssl-key key.pem")

    # Get network addresses
    import socket
    import subprocess

    # Run server
    logger.info(f"Starting server on {args.host}:{args.port}")
    logger.info("")
    logger.info("=" * 70)
    logger.info("Access the server at:")
    logger.info(f"  Local:   {protocol}://localhost:{args.port}")

    # Get all network interfaces using hostname -I (more reliable)
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            ips = result.stdout.strip().split()
            for ip in ips:
                # Filter out loopback and docker bridges (172.17.x.x)
                if not ip.startswith("127.") and not ip.startswith("172.17."):
                    logger.info(f"  Network: {protocol}://{ip}:{args.port}")
    except Exception:

        # Fallback to socket method
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and ip != "127.0.0.1":
                logger.info(f"  Network: {protocol}://{ip}:{args.port}")
        except Exception:

            pass

    logger.info("=" * 70)
    logger.info("")
    logger.info("Press Ctrl+C to stop")

    # Setup signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        logger.info("\nReceived signal to terminate. Shutting down gracefully...")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_context)
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")


if __name__ == "__main__":
    main()
