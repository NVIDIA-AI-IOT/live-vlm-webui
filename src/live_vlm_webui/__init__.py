"""
Live VLM WebUI - Real-time Vision Language Model interaction web interface.

A universal web interface for streaming webcam feeds to Vision Language Models
with real-time AI analysis and system monitoring.
"""

__version__ = "0.1.0"
__author__ = "NVIDIA Corporation"
__license__ = "Apache-2.0"

from . import server
from . import video_processor
from . import gpu_monitor
from . import vlm_service

__all__ = ["server", "video_processor", "gpu_monitor", "vlm_service"]
