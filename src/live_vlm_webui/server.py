# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
WebRTC Live VLM WebUI Server
Main server that handles WebRTC connections and serves the web interface
"""

import asyncio
import ipaddress
import json
import logging
import os
import signal
import socket
import subprocess

import aiohttp
from aiohttp import web
from aiortc import (
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.contrib.media import MediaRelay

from .gpu_monitor import create_monitor
from .multi_camera import (
    MULTI_CAMERA_KEY,
    VLM_FIELDS,
    MultiCameraVLMService,
    register_multi_camera,
    validate_id,
)
from .rtsp_track import RTSPVideoTrack
from .temporal_buffer import TemporalConfig
from .video_processor import VideoProcessorTrack
from .vlm_service import VLMService

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
websocket_streams = {}
SINGLE_PROCESSORS_KEY = web.AppKey("single_processors", dict)
TEMPORAL_DEFAULT_KEY = web.AppKey("temporal_default", dict)
gpu_monitor = None  # GPU monitoring instance
gpu_monitor_task = None  # Background task for GPU monitoring
rtsp_tracks = {}  # Track active RTSP streams {session_id: (rtsp_track, processor_track)}


def is_port_available(port, host="0.0.0.0"):
    """Check if a port is available for binding"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.close()
        return True
    except OSError:
        return False


def is_loopback_host(host):
    """Return True when a server host value is limited to localhost."""
    normalized = (host or "").strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
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


async def multi_camera_asset(request):
    """Serve the multi-camera page and its local assets."""
    names = {
        "/multi-camera": "multi-camera.html",
        "/multi-camera.js": "multi-camera.js",
        "/multi-camera.css": "multi-camera.css",
    }
    return web.FileResponse(os.path.join(os.path.dirname(__file__), "static", names[request.path]))


async def shared_webui_asset(request):
    """Serve the shared single-camera and multi-stream shell assets."""
    names = {"/webui.css": "webui.css", "/webui.js": "webui.js"}
    return web.FileResponse(os.path.join(os.path.dirname(__file__), "static", names[request.path]))


async def models(request):
    """Return available models and the server's configured request backend."""
    configuration = {
        "backend": getattr(vlm_service, "backend", "openai"),
        "api_base": vlm_service.api_base if vlm_service else None,
        "configured_model": vlm_service.model if vlm_service else None,
    }
    try:
        # Check if custom API base and key are provided in query params
        api_base = request.rel_url.query.get("api_base")
        api_key = request.rel_url.query.get("api_key")

        if api_base:
            configuration["queried_api_base"] = api_base
            # Query models from the provided API endpoint
            from openai import AsyncOpenAI

            temp_client = AsyncOpenAI(base_url=api_base, api_key=api_key if api_key else "EMPTY")
            models_response = await temp_client.models.list()
            models_list = [
                {"id": model.id, "name": model.id, "current": False}
                for model in models_response.data
            ]
            return web.Response(
                content_type="application/json",
                text=json.dumps({"models": models_list, **configuration}),
            )
        elif vlm_service:
            # Use the server's VLM service
            models_response = await vlm_service.client.models.list()
            models_list = [
                {"id": model.id, "name": model.id, "current": model.id == vlm_service.model}
                for model in models_response.data
            ]
            return web.Response(
                content_type="application/json",
                text=json.dumps({"models": models_list, **configuration}),
            )
        else:
            return web.Response(
                content_type="application/json",
                text=json.dumps(
                    {"models": [], "error": "VLM service not initialized", **configuration}
                ),
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
                        ],
                        **configuration,
                    }
                ),
            )
        return web.Response(
            content_type="application/json",
            text=json.dumps({"models": [], "error": str(e), **configuration}),
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


def _single_services(request, stream_id):
    services = [vlm_service] if vlm_service is not None else []
    services.extend(
        processor.vlm_service
        for processor, owner in request.app[SINGLE_PROCESSORS_KEY].items()
        if owner == stream_id and processor.vlm_service is not None
    )
    return services


def _reset_single_context(request, stream_id):
    for processor, owner in request.app[SINGLE_PROCESSORS_KEY].items():
        if owner == stream_id:
            processor.reset_temporal()


def _single_processor(request, track, temporal, stream_id, source_generation_provider=None):
    service = None
    if vlm_service is not None:
        service = MultiCameraVLMService(
            semaphore=request.app[MULTI_CAMERA_KEY].semaphore,
            on_result=lambda _text, _metrics: None,
            **{name: getattr(vlm_service, name) for name in VLM_FIELDS},
        )
    processor = VideoProcessorTrack(
        track,
        service,
        temporal=temporal,
        source_generation_provider=source_generation_provider,
        text_callback=lambda text, metrics: broadcast_text_update(
            text, metrics, stream_id=stream_id
        ),
        temporal_callback=lambda status: _broadcast_stream_message(
            {"type": "temporal_status", **status}, stream_id
        ),
    )
    request.app[SINGLE_PROCESSORS_KEY][processor] = stream_id
    return processor


async def _close_single_processor(app, processor):
    task = getattr(processor, "_single_close_task", None)
    if task is None:
        if processor not in app[SINGLE_PROCESSORS_KEY]:
            return

        async def release():
            try:
                await processor.aclose()
            finally:
                # The relay/source is separately owned. Close only this API client.
                if processor.vlm_service is not None:
                    await processor.vlm_service.client.close()
                app[SINGLE_PROCESSORS_KEY].pop(processor, None)

        task = asyncio.create_task(release())
        processor._single_close_task = task
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # Peer/source/shutdown can race. One owner completes cancellation and
        # releases the API client before another request can reuse the source.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


async def _close_single_processors(app):
    await asyncio.gather(
        *(_close_single_processor(app, processor) for processor in list(app[SINGLE_PROCESSORS_KEY]))
    )


async def websocket_handler(request):
    """Handle WebSocket connections for text updates"""
    stream_id = request.query.get("stream_id")
    if stream_id is not None:
        try:
            validate_id(stream_id, "stream_id")
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    websocket_streams[ws] = stream_id

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
                    "backend": getattr(vlm_service, "backend", "openai"),
                    "prompt": vlm_service.prompt,
                    "max_tokens": vlm_service.max_tokens,
                    "process_every": VideoProcessorTrack.process_every_n_frames,
                    "temporal": request.app[TEMPORAL_DEFAULT_KEY]["config"].as_dict(),
                }
            )

        await ws.send_json(
            {
                "type": "temporal_config",
                "temporal": request.app[TEMPORAL_DEFAULT_KEY]["config"].as_dict(),
            }
        )

        # Keep connection alive and handle incoming messages
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)

                    if data.get("type") == "update_temporal":
                        try:
                            temporal = TemporalConfig.from_dict(data.get("temporal"))
                            if any(
                                owner == stream_id and processor.readyState == "live"
                                for processor, owner in request.app[SINGLE_PROCESSORS_KEY].items()
                            ):
                                raise ValueError(
                                    "Stop the stream before changing video understanding"
                                )
                            request.app[TEMPORAL_DEFAULT_KEY]["config"] = temporal
                            await ws.send_json(
                                {"type": "temporal_updated", "temporal": temporal.as_dict()}
                            )
                        except (TypeError, ValueError) as exc:
                            await ws.send_json(
                                {"type": "error", "error": str(exc), "text": str(exc)}
                            )

                    elif data.get("type") == "update_prompt":
                        new_prompt = data.get("prompt", "").strip()
                        max_tokens = data.get("max_tokens")
                        if new_prompt and vlm_service:
                            for service in _single_services(request, stream_id):
                                service.update_prompt(new_prompt, max_tokens)
                            _reset_single_context(request, stream_id)
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
                            for service in _single_services(request, stream_id):
                                service.model = new_model

                            # Update API settings if provided
                            # User may have switched to different service (Ollama ↔ vLLM)
                            if api_base:
                                for service in _single_services(request, stream_id):
                                    service.update_api_settings(
                                        api_base, api_key if api_key else None
                                    )
                                logger.info(f"Model updated: {new_model}, API: {api_base}")
                            else:
                                logger.info(f"Model updated: {new_model}")

                            _reset_single_context(request, stream_id)
                            # Confirm to client
                            await ws.send_json(
                                {
                                    "type": "model_updated",
                                    "model": new_model,
                                    "api_base": vlm_service.api_base,
                                    "backend": getattr(vlm_service, "backend", "openai"),
                                }
                            )

                    elif data.get("type") == "update_processing":
                        process_every = data.get("process_every", 30)
                        try:
                            process_every = int(process_every)
                            if 1 <= process_every <= 3600:  # Up to 3600 frames (2 minutes @ 30fps)
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
        websocket_streams.pop(ws, None)
        logger.info(f"WebSocket client disconnected. Total clients: {len(websockets)}")

    return ws


def _broadcast_stream_message(message, stream_id=None):
    encoded = json.dumps(message)
    for ws in list(websockets):
        # Unidentified legacy viewers share only the legacy None scope. Their
        # source updates must never overwrite explicitly identified streams.
        if ws.closed or websocket_streams.get(ws) != stream_id:
            continue
        asyncio.create_task(ws.send_str(encoded))


def broadcast_text_update(text: str, metrics: dict, *, stream_id=None):
    """Send each stream's results to its own WebSocket viewer."""
    _broadcast_stream_message({"type": "vlm_response", "text": text, "metrics": metrics}, stream_id)


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
    """Handle WebRTC offer from client (supports both webcam and RTSP)"""
    params = await request.json()
    try:
        temporal = TemporalConfig.from_dict(
            params.get("temporal", request.app[TEMPORAL_DEFAULT_KEY]["config"])
        )
        stream_id = params.get("stream_id")
        if stream_id is not None:
            validate_id(stream_id, "stream_id")
    except (TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    offer_sdp = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    rtsp_url = params.get("rtsp_url")  # Optional RTSP URL for IP camera mode

    # Create RTCPeerConnection with STUN servers for Docker/NAT compatibility
    config = RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
        ]
    )
    pc = RTCPeerConnection(configuration=config)
    pcs.add(pc)

    # A peer owns its processors and optional RTSP source until one complete
    # teardown, even if negotiation or its caller is cancelled repeatedly.
    rtsp_cleanup_track = None
    peer_processors = []
    peer_close_task = None

    async def close_peer():
        nonlocal peer_close_task
        if peer_close_task is None:

            async def release():
                actions = [
                    lambda processor=processor: _close_single_processor(request.app, processor)
                    for processor in peer_processors
                ]
                if rtsp_cleanup_track is not None:
                    actions.append(getattr(rtsp_cleanup_track, "aclose", rtsp_cleanup_track.stop))
                actions.append(pc.close)
                try:
                    for action in actions:
                        try:
                            result = action()
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:  # Release remaining resources after a close failure.
                            logger.exception("Single-camera peer cleanup failed")
                finally:
                    pcs.discard(pc)

            peer_close_task = asyncio.create_task(release())
        try:
            await asyncio.shield(peer_close_task)
        except asyncio.CancelledError:
            while not peer_close_task.done():
                try:
                    await asyncio.shield(peer_close_task)
                except asyncio.CancelledError:
                    continue
            peer_close_task.result()
            raise

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"Connection state: {pc.connectionState}")
        if pc.connectionState in ["failed", "closed"] and peer_close_task is None:
            # Closing the peer also emits this event. Its existing cleanup owner
            # must never await itself through that callback.
            await close_peer()

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logger.info(f"ICE connection state: {pc.iceConnectionState}")
        if pc.iceConnectionState == "failed":
            logger.error("ICE connection failed - check firewall/NAT settings")

    @pc.on("icegatheringstatechange")
    async def on_icegatheringstatechange():
        logger.info(f"ICE gathering state: {pc.iceGatheringState}")

    try:
        # If RTSP URL provided, create a track instead of waiting for the browser.
        if rtsp_url:
            logger.info(f"Creating RTSP track for: {rtsp_url}")
            try:
                rtsp_track = RTSPVideoTrack(rtsp_url)
                rtsp_cleanup_track = rtsp_track

                # Include this setup wait in cancellation-owned teardown.
                await asyncio.sleep(0.5)
                # This peer is the sole reader. Consuming the owned source
                # directly keeps reconnect generations paired with their frames.
                processor_track = _single_processor(request, rtsp_track, temporal, stream_id)
                peer_processors.append(processor_track)
                pc.addTrack(processor_track)
                logger.info("Added RTSP processor track to peer connection")
            except Exception as exc:
                logger.error("Failed to create RTSP track: %s", exc)
                await close_peer()
                return web.json_response(
                    {"error": f"Failed to connect to RTSP stream: {exc}"}, status=500
                )
        else:

            @pc.on("track")
            def on_track(track):
                logger.info(f"Received track: {track.kind}")
                if peer_close_task is not None:
                    track.stop()
                    return
                if track.kind == "video":
                    processor_track = _single_processor(
                        request, relay.subscribe(track), temporal, stream_id
                    )
                    peer_processors.append(processor_track)
                    pc.addTrack(processor_track)
                    logger.info("Added processed video track back to peer connection")

                @track.on("ended")
                async def on_ended():
                    logger.info(f"Track {track.kind} ended")
                    if track.kind == "video":
                        await _close_single_processor(request.app, processor_track)

        await pc.setRemoteDescription(offer_sdp)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        logger.info(f"Created answer with {len(pc.getTransceivers())} transceivers")
        return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
    except BaseException:
        await close_peer()
        raise


async def rtsp_start(request):
    """
    Start RTSP stream processing.

    Accepts RTSP URL and creates a video processing pipeline.

    POST /api/rtsp/start
    Body: {"rtsp_url": "rtsp://...", "session_id": "optional-id"}
    """
    try:
        data = await request.json()
        rtsp_url = data.get("rtsp_url")
        session_id = data.get("session_id", "default")
        try:
            temporal = TemporalConfig.from_dict(
                data.get("temporal", request.app[TEMPORAL_DEFAULT_KEY]["config"])
            )
            stream_id = data.get("stream_id")
            if stream_id is not None:
                validate_id(stream_id, "stream_id")
        except (TypeError, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

        if not rtsp_url:
            logger.warning("RTSP start request missing rtsp_url")
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps({"error": "Missing rtsp_url parameter"}),
            )

        # Check if session already exists
        if session_id in rtsp_tracks:
            logger.warning(f"RTSP session {session_id} already exists, stopping it first")
            await _stop_rtsp_session(session_id)

        logger.info(f"Starting RTSP stream for session {session_id}")

        # Create RTSP video track
        try:
            rtsp_track = RTSPVideoTrack(rtsp_url)
        except Exception as e:
            logger.error(f"Failed to create RTSP track: {e}")
            return web.Response(
                status=500,
                content_type="application/json",
                text=json.dumps({"error": f"Failed to connect to RTSP stream: {str(e)}"}),
            )

        # Create processor track (same as WebRTC path)
        processor_track = _single_processor(request, rtsp_track, temporal, stream_id)
        processor_track._single_app = request.app

        # Start background task to consume frames
        async def consume_frames():
            """Background task to continuously pull frames from processor track"""
            try:
                while not rtsp_track._stopped:
                    try:
                        _ = await processor_track.recv()
                        # Frame is processed, just discard it (VLM analysis happens in recv())
                    except StopAsyncIteration:
                        logger.info(f"RTSP stream {session_id} ended")
                        break
                    except Exception as e:
                        logger.error(f"Error consuming RTSP frame for {session_id}: {e}")
                        break
            finally:
                await _close_single_processor(request.app, processor_track)
                rtsp_track.stop()
                logger.info(f"Frame consumption stopped for {session_id}")

        frame_task = asyncio.create_task(consume_frames())

        # Store reference with frame task
        rtsp_tracks[session_id] = (rtsp_track, processor_track, frame_task)

        # Get stream stats
        stats = rtsp_track.get_stats()

        logger.info(
            f"RTSP stream started: {session_id} - {stats.get('codec')} "
            f"{stats.get('width')}x{stats.get('height')}"
        )

        return web.Response(
            content_type="application/json",
            text=json.dumps({"status": "started", "session_id": session_id, "stream_info": stats}),
        )

    except Exception as e:
        logger.error(f"Error starting RTSP: {e}", exc_info=True)
        return web.Response(
            status=500, content_type="application/json", text=json.dumps({"error": str(e)})
        )


async def rtsp_stop(request):
    """
    Stop RTSP stream processing.

    POST /api/rtsp/stop
    Body: {"session_id": "optional-id"}
    """
    try:
        data = await request.json()
        session_id = data.get("session_id", "default")

        await _stop_rtsp_session(session_id)

        return web.Response(
            content_type="application/json",
            text=json.dumps({"status": "stopped", "session_id": session_id}),
        )

    except Exception as e:
        logger.error(f"Error stopping RTSP: {e}", exc_info=True)
        return web.Response(
            status=500, content_type="application/json", text=json.dumps({"error": str(e)})
        )


async def rtsp_status(request):
    """
    Get status of all RTSP streams.

    GET /api/rtsp/status
    """
    try:
        status_list = []

        for session_id, (rtsp_track, processor_track, frame_task) in rtsp_tracks.items():
            stats = rtsp_track.get_stats()
            status_list.append(
                {
                    "session_id": session_id,
                    "connected": stats.get("connected"),
                    "frames_received": stats.get("frames_received"),
                    "stream_info": {
                        "codec": stats.get("codec"),
                        "width": stats.get("width"),
                        "height": stats.get("height"),
                        "fps": stats.get("fps"),
                    },
                }
            )

        return web.Response(
            content_type="application/json",
            text=json.dumps({"active_streams": len(rtsp_tracks), "streams": status_list}),
        )

    except Exception as e:
        logger.error(f"Error getting RTSP status: {e}", exc_info=True)
        return web.Response(
            status=500, content_type="application/json", text=json.dumps({"error": str(e)})
        )


async def _stop_rtsp_session(session_id: str):
    """Helper function to stop an RTSP session"""
    if session_id in rtsp_tracks:
        rtsp_track, processor_track, frame_task = rtsp_tracks[session_id]

        # Cancel frame consumption task
        if frame_task and not frame_task.done():
            frame_task.cancel()
            try:
                await frame_task
            except asyncio.CancelledError:
                pass

        # Stop tracks
        try:
            single_app = getattr(processor_track, "_single_app", None)
            if single_app is not None:
                await _close_single_processor(single_app, processor_track)
            else:
                await processor_track.aclose()
        except Exception as e:
            logger.warning(f"Error stopping processor track: {e}")

        try:
            rtsp_track.stop()
        except Exception as e:
            logger.warning(f"Error stopping RTSP track: {e}")

        # Remove from tracking
        del rtsp_tracks[session_id]
        logger.info(f"RTSP stream stopped: {session_id}")
    else:
        logger.warning(f"RTSP session {session_id} not found")


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
    websocket_streams.clear()

    # Close all RTSP streams
    for session_id in list(rtsp_tracks.keys()):
        await _stop_rtsp_session(session_id)
    logger.info("RTSP streams closed")

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
    app[SINGLE_PROCESSORS_KEY] = {}
    app[TEMPORAL_DEFAULT_KEY] = {"config": TemporalConfig()}
    app.on_cleanup.append(_close_single_processors)
    app.router.add_get("/", index)
    for path in ("/webui.css", "/webui.js"):
        app.router.add_get(path, shared_webui_asset)
    for path in ("/multi-camera", "/multi-camera.js", "/multi-camera.css"):
        app.router.add_get(path, multi_camera_asset)
    app.router.add_get("/models", models)
    app.router.add_get("/detect-services", detect_services)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_post("/offer", offer)

    # RTSP endpoints
    app.router.add_post("/api/rtsp/start", rtsp_start)
    app.router.add_post("/api/rtsp/stop", rtsp_stop)
    app.router.add_get("/api/rtsp/status", rtsp_status)

    register_multi_camera(app, defaults_provider=lambda: vlm_service)

    # Serve static files (images, etc.)
    # Always serve from static/images within the package (works for both pip and dev installs)
    images_dir = os.path.join(os.path.dirname(__file__), "static", "images")
    images_dir = os.path.abspath(images_dir)

    if os.path.exists(images_dir):
        app.router.add_static("/images", images_dir, name="images")
        logger.info(f"Serving static files from: {images_dir}")
    else:
        logger.warning(f"⚠️  Static images directory not found: {images_dir}")

    # Serve favicon files
    favicon_dir = os.path.join(os.path.dirname(__file__), "static", "favicon")
    favicon_dir = os.path.abspath(favicon_dir)

    if os.path.exists(favicon_dir):
        app.router.add_static("/favicon", favicon_dir, name="favicon")
        logger.info(f"Serving favicon files from: {favicon_dir}")
    else:
        logger.warning(f"⚠️  Favicon directory not found: {favicon_dir}")

    if not test_mode:
        app.on_startup.append(on_startup)
        app.on_shutdown.append(on_shutdown)

    return app


def get_app_config_dir():
    """Get the application config directory following OS conventions"""
    import os
    from pathlib import Path

    # Follow XDG Base Directory spec on Linux, use OS-appropriate paths elsewhere
    if os.name == "posix":
        if "darwin" in os.sys.platform.lower():
            # macOS
            config_dir = Path.home() / "Library" / "Application Support" / "live-vlm-webui"
        else:
            # Linux/Unix (including Jetson)
            config_dir = (
                Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "live-vlm-webui"
            )
    else:
        # Windows
        config_dir = Path(os.environ.get("APPDATA", Path.home())) / "live-vlm-webui"

    # Create directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def generate_self_signed_cert(cert_path="cert.pem", key_path="key.pem"):
    """Generate a self-signed SSL certificate if it doesn't exist"""
    import os
    import subprocess

    if os.path.exists(cert_path) and os.path.exists(key_path):
        return True

    logger.info("🔐 Generating self-signed SSL certificate...")
    logger.info(f"   Saving to: {os.path.dirname(os.path.abspath(cert_path)) or '.'}")
    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:4096",
                "-nodes",
                "-out",
                cert_path,
                "-keyout",
                key_path,
                "-days",
                "365",
                "-subj",
                "/CN=localhost",
            ],
            check=True,
            capture_output=True,
        )
        logger.info(f"✅ Generated {cert_path} and {key_path}")
        return True
    except FileNotFoundError:
        logger.warning("⚠️  openssl not found - cannot auto-generate certificates")
        logger.warning(
            "⚠️  Install openssl: sudo apt install openssl (Linux) or brew install openssl (Mac)"
        )
        return False
    except subprocess.CalledProcessError as e:
        logger.warning(f"⚠️  Failed to generate certificates: {e}")
        return False


def main():
    """Main entry point"""
    import argparse
    import ssl

    from . import __version__

    parser = argparse.ArgumentParser(
        description="WebRTC Live VLM WebUI - Real-time vision model interaction",
        epilog="Examples:\n"
        "  vLLM:    python server.py --model llama-3.2-11b-vision-instruct --api-base http://localhost:8000/v1\n"
        "  SGLang:  python server.py --model llama-3.2-11b-vision-instruct --api-base http://localhost:30000/v1\n"
        "  Ollama:  python server.py --model llava:7b --api-base http://localhost:11434/v1\n"
        "  HTTPS:   python server.py --model llava:7b --api-base http://localhost:11434/v1 --ssl-cert cert.pem --ssl-key key.pem\n"
        "  Kiosk:   python server.py --localhost-http --model llava:7b --api-base http://localhost:11434/v1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    default_prompt = os.environ.get(
        "LIVE_VLM_DEFAULT_PROMPT",
        "Describe what you see in this image in one sentence.",
    )
    try:
        default_max_tokens = int(os.environ.get("LIVE_VLM_MAX_TOKENS", "512"))
    except ValueError:
        logger.warning("Invalid LIVE_VLM_MAX_TOKENS value; using 512")
        default_max_tokens = 512
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
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
        "--vlm-backend",
        choices=("openai", "edgellm"),
        default="openai",
        help="VLM request dialect (default: openai; use edgellm for TensorRT Edge-LLM)",
    )
    parser.add_argument(
        "--api-key",
        default="EMPTY",
        help="API key - use 'EMPTY' for local servers, required for NVIDIA NGC/OpenAI (default: EMPTY)",
    )
    parser.add_argument(
        "--prompt",
        default=default_prompt,
        help="Prompt to send to VLM (default can be set with LIVE_VLM_DEFAULT_PROMPT)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=default_max_tokens,
        help="Maximum tokens to generate (default: 512, or LIVE_VLM_MAX_TOKENS)",
    )
    # Get default SSL cert paths (platform-specific)
    default_config_dir = get_app_config_dir()
    default_cert_path = str(default_config_dir / "cert.pem")
    default_key_path = str(default_config_dir / "key.pem")

    parser.add_argument("--process-every", type=int, default=30, help="Process every Nth frame")
    parser.add_argument(
        "--ssl-cert",
        default=None,  # Will be set to config dir if not specified
        help=f"Path to SSL certificate file (default: {default_cert_path}, auto-generated if missing)",
    )
    parser.add_argument(
        "--ssl-key",
        default=None,  # Will be set to config dir if not specified
        help=f"Path to SSL private key file (default: {default_key_path}, auto-generated if missing)",
    )
    parser.add_argument(
        "--no-ssl",
        action="store_true",
        help="Disable SSL (webcam works on http://localhost; remote access requires HTTPS)",
    )
    parser.add_argument(
        "--localhost-http",
        action="store_true",
        help=(
            "Serve HTTP on 127.0.0.1 for same-device kiosk use. "
            "This avoids self-signed certificate prompts while keeping the browser on localhost."
        ),
    )

    args = parser.parse_args()
    if args.max_tokens < 1:
        parser.error("--max-tokens must be >= 1")
    if args.localhost_http:
        args.host = "127.0.0.1"
        args.no_ssl = True

    # Set default SSL cert paths to config directory if not specified
    if args.ssl_cert is None:
        config_dir = get_app_config_dir()
        args.ssl_cert = str(config_dir / "cert.pem")
    if args.ssl_key is None:
        config_dir = get_app_config_dir()
        args.ssl_key = str(config_dir / "key.pem")

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
    vlm_service = VLMService(
        model=model,
        api_base=api_base,
        api_key=api_key,
        prompt=args.prompt,
        max_tokens=args.max_tokens,
        backend=args.vlm_backend,
    )

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

    # Setup SSL (auto-generate certificates if needed)
    ssl_context = None
    protocol = "http"
    if not args.no_ssl:
        # Try to auto-generate if certificates don't exist
        import sys

        if not os.path.exists(args.ssl_cert) or not os.path.exists(args.ssl_key):
            success = generate_self_signed_cert(args.ssl_cert, args.ssl_key)
            if not success:
                # FAIL FAST - SSL is required for webcam access
                logger.error("")
                logger.error("❌ Cannot start server without SSL certificates")
                logger.error("❌ Remote webcam access requires HTTPS!")
                logger.error("")
                logger.error("🔧 To fix, install openssl:")
                logger.error("   Linux/Jetson: sudo apt install openssl")
                logger.error("   macOS: brew install openssl")
                logger.error("")
                logger.error("   Then restart the server")
                logger.error("")
                logger.error("⚠️  For a local kiosk, use --localhost-http and http://localhost")
                logger.error("")
                sys.exit(1)

        # Load certificates (they must exist at this point)
        if os.path.exists(args.ssl_cert) and os.path.exists(args.ssl_key):
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(args.ssl_cert, args.ssl_key)
            protocol = "https"
            logger.info("SSL enabled - using HTTPS")
        else:
            # This should never happen, but just in case
            logger.error("❌ SSL certificates missing after generation - unexpected error")
            sys.exit(1)
    else:
        logger.warning("⚠️  SSL disabled with --no-ssl flag")
        if args.localhost_http or is_loopback_host(args.host):
            logger.info("Using localhost HTTP mode for same-device browser access")
            logger.info("Open http://localhost:%s on this machine for webcam access", args.port)
        else:
            logger.warning("⚠️  Webcam access may not work from non-local HTTP origins")
            logger.warning("⚠️  Use HTTPS for browsers connecting from another device")

    # Get network addresses
    import socket
    import subprocess

    # Run server
    logger.info(f"Starting server on {args.host}:{args.port}")
    logger.info("")
    logger.info("=" * 70)
    logger.info("Access the server at:")
    logger.info(f"  Local:   {protocol}://localhost:{args.port}")

    # Get network interfaces - try multiple methods for cross-platform support
    network_ips = []

    # Method 1: hostname -I (Linux)
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            ips = result.stdout.strip().split()
            for ip in ips:
                # Filter out loopback and docker bridges (172.17.x.x)
                if not ip.startswith("127.") and not ip.startswith("172.17."):
                    network_ips.append(ip)
    except Exception:
        pass

    # Method 2: Socket method (cross-platform fallback)
    if not network_ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and ip != "127.0.0.1":
                network_ips.append(ip)
        except Exception:
            pass

    # Display all found network IPs
    if not is_loopback_host(args.host):
        for ip in network_ips:
            logger.info(f"  Network: {protocol}://{ip}:{args.port}")

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


def stop():
    """Stop the running live-vlm-webui server"""
    import sys
    import time

    try:
        import psutil
    except ImportError:
        logger.error("psutil is required for the stop command")
        logger.error("Install it with: pip install live-vlm-webui[dev]")
        sys.exit(1)

    print("Stopping Live VLM WebUI server...")

    # Find and kill processes running live_vlm_webui.server
    found = False
    killed = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline")
            if cmdline:
                cmdline_str = " ".join(cmdline)
                if "live_vlm_webui.server" in cmdline_str or "live-vlm-webui" in cmdline_str:
                    # Don't kill the stop command itself
                    if "stop" not in cmdline_str:
                        found = True
                        print(f"  Stopping process {proc.info['pid']}: {proc.info['name']}")
                        proc.terminate()
                        killed.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    if not found:
        print("✓ No running server found")
        return

    # Wait for graceful shutdown
    time.sleep(2)

    # Force kill if still running
    for proc in killed:
        try:
            if proc.is_running():
                print(f"  Force killing process {proc.pid}")
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Final verification
    time.sleep(1)
    still_running = False
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = proc.info.get("cmdline")
            if cmdline:
                cmdline_str = " ".join(cmdline)
                if "live_vlm_webui.server" in cmdline_str or "live-vlm-webui" in cmdline_str:
                    if "stop" not in cmdline_str:
                        still_running = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    if still_running:
        print("❌ Failed to stop server")
        sys.exit(1)
    else:
        print("✓ Server stopped successfully")


if __name__ == "__main__":
    main()
