"""
GPU Monitoring Module
Supports multiple platforms: NVIDIA (NVML), Jetson Thor (jtop), Jetson Orin (jtop), Apple Silicon, AMD
"""

import logging
import os
import platform
import psutil
import socket
import subprocess
from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from collections import deque

logger = logging.getLogger(__name__)


def get_cpu_model() -> str:
    """
    Get CPU model name in a cross-platform way

    Returns:
        CPU model string, or 'Unknown CPU' if not available
    """
    try:
        # Try different methods based on platform
        system = platform.system()

        if system == "Linux":
            # Read from /proc/cpuinfo
            try:
                with open("/proc/cpuinfo", "r") as f:
                    for line in f:
                        if line.startswith("model name"):
                            return line.split(":")[1].strip()
            except Exception:

                pass

        elif system == "Darwin":  # macOS
            # Use sysctl to get CPU brand string
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            except Exception:

                pass

        elif system == "Windows":
            # Use WMIC
            try:
                result = subprocess.run(
                    ["wmic", "cpu", "get", "name"], capture_output=True, text=True, timeout=1
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split("\n")
                    if len(lines) > 1:
                        return lines[1].strip()
            except Exception:

                pass

        # Fallback to platform.processor()
        proc = platform.processor()
        if proc and proc.strip():
            return proc.strip()

        return "Unknown CPU"

    except Exception as e:
        logger.warning(f"Failed to get CPU model: {e}")
        return "Unknown CPU"


class GPUMonitor(ABC):
    """Abstract base class for GPU monitoring"""

    def __init__(self, history_size: int = 60):
        """
        Initialize GPU monitor

        Args:
            history_size: Number of historical data points to keep (default 60 = 1 minute at 1Hz)
        """
        self.history_size = history_size
        self.gpu_util_history = deque(maxlen=history_size)
        self.vram_used_history = deque(maxlen=history_size)
        self.cpu_util_history = deque(maxlen=history_size)
        self.ram_used_history = deque(maxlen=history_size)

    @abstractmethod
    def get_stats(self) -> Dict:
        """Get current GPU and system stats"""
        pass

    @abstractmethod
    def cleanup(self):
        """Cleanup resources"""
        pass

    def get_cpu_ram_stats(self) -> Dict:
        """Get CPU and RAM stats (common across all platforms)"""
        try:
            # Use interval=None for non-blocking call
            # First call returns 0.0, subsequent calls return percentage since last call
            cpu_percent = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            hostname = socket.gethostname()
            cpu_model = get_cpu_model()

            return {
                "cpu_percent": cpu_percent,
                "cpu_model": cpu_model,
                "ram_used_gb": memory.used / (1024**3),
                "ram_total_gb": memory.total / (1024**3),
                "ram_percent": memory.percent,
                "hostname": hostname,
            }
        except Exception as e:
            logger.error(f"Error getting CPU/RAM stats: {e}")
            return {
                "cpu_percent": 0,
                "cpu_model": "Unknown CPU",
                "ram_used_gb": 0,
                "ram_total_gb": 0,
                "ram_percent": 0,
                "hostname": "Unknown",
            }

    def update_history(self, stats: Dict):
        """Update historical data"""
        self.gpu_util_history.append(stats.get("gpu_percent", 0))
        self.vram_used_history.append(stats.get("vram_used_gb", 0))
        self.cpu_util_history.append(stats.get("cpu_percent", 0))
        self.ram_used_history.append(stats.get("ram_used_gb", 0))

    def get_history(self) -> Dict[str, List[float]]:
        """Get historical data as lists"""
        return {
            "gpu_util": list(self.gpu_util_history),
            "vram_used": list(self.vram_used_history),
            "cpu_util": list(self.cpu_util_history),
            "ram_used": list(self.ram_used_history),
        }


class NVMLMonitor(GPUMonitor):
    """NVIDIA GPU monitoring using NVML (for Desktop, DGX, Jetson Thor)"""

    def __init__(self, device_index: int = 0, history_size: int = 60):
        """
        Initialize NVML monitor

        Args:
            device_index: GPU device index (default 0)
            history_size: Number of historical data points to keep
        """
        super().__init__(history_size)
        self.device_index = device_index
        self.handle = None
        self.available = False
        self.error_logged = False  # Track if we've already logged an error
        self.consecutive_errors = 0  # Count consecutive errors

        # Detect DGX Spark
        self.product_name = ""
        self.dgx_version = ""
        self.vram_warning_logged = False  # Track if we've warned about VRAM not supported
        try:
            with open("/etc/dgx-release", "r") as f:
                for line in f:
                    if line.startswith("DGX_PRETTY_NAME="):
                        self.product_name = line.split("=")[1].strip().strip('"')
                    elif line.startswith("DGX_SWBUILD_VERSION="):
                        self.dgx_version = line.split("=")[1].strip().strip('"')
            if self.product_name:
                logger.info(f"Detected {self.product_name} (Version {self.dgx_version})")
        except FileNotFoundError:
            pass  # Not a DGX system
        except Exception as e:
            logger.debug(f"Could not read DGX info: {e}")

        try:
            import pynvml

            pynvml.nvmlInit()
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
            self.device_name = pynvml.nvmlDeviceGetName(self.handle)
            if isinstance(self.device_name, bytes):
                self.device_name = self.device_name.decode("utf-8")
            self.available = True
            logger.info(f"NVML initialized for GPU: {self.device_name}")

            # Check if this is Jetson Thor (which may have limited NVML support)
            if "Thor" in self.device_name:
                logger.warning(f"Detected {self.device_name} - NVML support may be limited")
        except Exception as e:
            logger.warning(f"NVML not available: {e}")
            self.available = False
            self.error_logged = True

    def get_stats(self) -> Dict:
        """Get current GPU stats using NVML"""
        if not self.available:
            return self._get_fallback_stats()

        try:
            import pynvml

            # Get GPU utilization
            utilization = pynvml.nvmlDeviceGetUtilizationRates(self.handle)
            gpu_percent = utilization.gpu

            # Get memory info (may not be supported on newer GPUs like GB10/Blackwell)
            try:
                memory_info = pynvml.nvmlDeviceGetMemoryInfo(self.handle)
                vram_used_gb = memory_info.used / (1024**3)
                vram_total_gb = memory_info.total / (1024**3)
                vram_percent = (memory_info.used / memory_info.total) * 100
            except Exception as e:
                # GB10/Blackwell and some newer GPUs don't support memory queries
                if "Not Supported" in str(e) or "not supported" in str(e).lower():
                    if not self.vram_warning_logged:
                        logger.warning(
                            f"VRAM monitoring not supported on {self.device_name} (GB10/Blackwell limitation)"
                        )
                        self.vram_warning_logged = True
                    vram_used_gb = None
                    vram_total_gb = None
                    vram_percent = None
                else:
                    raise

            # Get temperature
            try:
                temp = pynvml.nvmlDeviceGetTemperature(self.handle, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:

                temp = None

            # Get power usage
            try:
                power_mw = pynvml.nvmlDeviceGetPowerUsage(self.handle)
                power_w = power_mw / 1000.0
            except Exception:

                power_w = None

            # Get CPU and RAM stats
            system_stats = self.get_cpu_ram_stats()

            stats = {
                "platform": "NVIDIA (NVML)",
                "gpu_name": self.device_name,
                "gpu_percent": gpu_percent,
                "vram_used_gb": vram_used_gb,
                "vram_total_gb": vram_total_gb,
                "vram_percent": vram_percent,
                "temp_c": temp,
                "power_w": power_w,
                "product_name": self.product_name,  # DGX Spark, etc.
                **system_stats,
            }

            # Update history
            self.update_history(stats)

            return stats

        except Exception as e:
            self.consecutive_errors += 1

            # Only log error once, or every 60 seconds (60 calls at 1Hz)
            if not self.error_logged:
                logger.error(f"Error getting NVML stats: {e}")
                logger.warning("GPU monitoring disabled - falling back to CPU/RAM only")
                self.error_logged = True
                self.available = False  # Don't try again
            elif self.consecutive_errors % 60 == 0:
                logger.warning(
                    f"NVML still unavailable ({self.consecutive_errors} consecutive errors)"
                )

            return self._get_fallback_stats()

    def _get_fallback_stats(self) -> Dict:
        """Fallback stats when GPU not available"""
        system_stats = self.get_cpu_ram_stats()

        # Use GPU name if we got it during init, otherwise show unavailable
        gpu_name = getattr(self, "device_name", "N/A")
        platform_name = (
            f"NVIDIA {gpu_name} (monitoring unavailable)"
            if gpu_name != "N/A"
            else "NVIDIA (NVML unavailable)"
        )
        product_name = getattr(self, "product_name", "")

        return {
            "platform": platform_name,
            "gpu_name": gpu_name,
            "product_name": product_name,
            "gpu_percent": 0,
            "vram_used_gb": 0,
            "vram_total_gb": 0,
            "vram_percent": 0,
            "temp_c": None,
            "power_w": None,
            **system_stats,
        }

    def cleanup(self):
        """Cleanup NVML resources"""
        if self.available:
            try:
                import pynvml

                pynvml.nvmlShutdown()
                logger.info("NVML shutdown complete")
            except Exception as e:
                logger.error(f"Error during NVML cleanup: {e}")


class JetsonThorMonitor(GPUMonitor):
    """Jetson Thor GPU monitoring using jtop (jetson_stats) with fallback to nvhost_podgov"""

    def __init__(self, history_size: int = 60):
        super().__init__(history_size)
        self.gpu_name = "NVIDIA Thor"
        self.available = False
        self.use_jtop = False
        self.jtop_instance = None

        # Try jtop first (best support for Thor - GPU, VRAM, temp, power)
        try:
            from jtop import jtop

            self.jtop_instance = jtop()
            self.jtop_instance.start()
            self.use_jtop = True
            self.available = True
            logger.info("Jetson Thor monitoring initialized - using jtop (jetson_stats)")
        except ImportError:
            logger.warning(
                "jtop (jetson_stats) not installed - install with: sudo pip3 install jetson-stats"
            )
        except Exception as e:
            logger.warning(f"jtop initialization failed: {e}")

        # Fallback to nvhost_podgov if jtop not available
        if not self.use_jtop:
            # Thor-specific paths (JetPack 7 / L4T r38.2)
            self.gpu_base_path = (
                "/sys/devices/platform/bus@0/d0b0000000.pcie/pci0000:00/0000:00:00.0/0000:01:00.0"
            )
            self.gpc_load_target = (
                f"{self.gpu_base_path}/gpu-gpc-0/devfreq/gpu-gpc-0/nvhost_podgov/load_target"
            )
            self.gpc_load_max = (
                f"{self.gpu_base_path}/gpu-gpc-0/devfreq/gpu-gpc-0/nvhost_podgov/load_max"
            )
            self.nvd_load_target = (
                f"{self.gpu_base_path}/gpu-nvd-0/devfreq/gpu-nvd-0/nvhost_podgov/load_target"
            )
            self.nvd_load_max = (
                f"{self.gpu_base_path}/gpu-nvd-0/devfreq/gpu-nvd-0/nvhost_podgov/load_max"
            )

            # Check if monitoring is available
            try:
                with open(self.gpc_load_target, "r") as f:
                    f.read()
                self.available = True
                logger.info(
                    "Jetson Thor monitoring initialized - using nvhost_podgov (limited stats)"
                )
                logger.info(
                    "💡 For full stats (GPU, VRAM, temp), install: sudo pip3 install jetson-stats"
                )
            except (FileNotFoundError, PermissionError) as e:
                logger.warning(f"Jetson Thor nvhost_podgov not accessible: {e}")
                self.available = False

    def get_stats(self) -> Dict:
        """Get current GPU stats for Jetson Thor"""
        system_stats = self.get_cpu_ram_stats()

        if not self.available:
            return {
                "platform": "Jetson Thor (monitoring unavailable)",
                "gpu_name": self.gpu_name,
                "gpu_percent": 0,
                "vram_used_gb": 0,
                "vram_total_gb": 0,
                "vram_percent": 0,
                **system_stats,
            }

        # Use jtop if available (full stats)
        if self.use_jtop and self.jtop_instance:
            try:
                # Get stats from jtop
                gpu_percent = self.jtop_instance.stats.get("GPU", 0)

                # Get memory stats (jtop uses shared memory on Jetson)
                memory = self.jtop_instance.memory
                # Thor uses unified memory, RAM is shared with GPU
                # jtop returns memory in KB, convert to GB (divide by 1024^2)
                vram_used_gb = memory.get("RAM", {}).get("used", 0) / (1024 * 1024)
                vram_total_gb = memory.get("RAM", {}).get("tot", 0) / (1024 * 1024)
                vram_percent = (vram_used_gb / vram_total_gb * 100) if vram_total_gb > 0 else 0

                # Temperature
                temp_c = None
                if hasattr(self.jtop_instance, "temperature"):
                    temps = self.jtop_instance.temperature
                    # Try to get GPU temp
                    temp_c = temps.get("GPU", temps.get("thermal", None))

                # Power
                power_w = None
                if hasattr(self.jtop_instance, "power"):
                    power = self.jtop_instance.power
                    # Sum all power rails if available
                    if isinstance(power, dict):
                        power_w = (
                            sum(p.get("power", 0) for p in power.values() if isinstance(p, dict))
                            / 1000
                        )  # mW to W

                # Get board name (e.g., "Jetson AGX Thor Developer Kit")
                board_name = None
                if hasattr(self.jtop_instance, "board"):
                    board_info = self.jtop_instance.board
                    if isinstance(board_info, dict):
                        # Debug: log board_info structure once
                        if not hasattr(self, "_board_info_logged"):
                            logger.info(f"Board info structure: {list(board_info.keys())}")
                            if "info" in board_info:
                                logger.info(
                                    f"Board info['info'] keys: {list(board_info['info'].keys()) if isinstance(board_info['info'], dict) else type(board_info['info'])}"
                                )
                            if "hardware" in board_info:
                                logger.info(f"Board info['hardware']: {board_info['hardware']}")
                            if "platform" in board_info:
                                logger.info(f"Board info['platform']: {board_info['platform']}")
                            self._board_info_logged = True

                        # Try to get board name from various possible locations
                        # Check 'hardware' dict first (jtop structure)
                        if "hardware" in board_info and isinstance(board_info["hardware"], dict):
                            board_name = board_info["hardware"].get("Model") or board_info[
                                "hardware"
                            ].get("Module")
                        # Fallback to 'info' dict if available
                        if (
                            not board_name
                            and "info" in board_info
                            and isinstance(board_info["info"], dict)
                        ):
                            board_name = board_info["info"].get("Machine") or board_info[
                                "info"
                            ].get("Model")
                        # Fallback to 'platform' if it's a string
                        if not board_name and "platform" in board_info:
                            platform = board_info["platform"]
                            if isinstance(platform, dict):
                                board_name = platform.get("Machine")
                            elif isinstance(platform, str):
                                board_name = platform

                        # Final fallback: if still not a string, stringify safely
                        if board_name and not isinstance(board_name, str):
                            logger.warning(
                                f"Board name is not a string: {type(board_name)}, value: {board_name}"
                            )
                            board_name = str(board_name) if board_name else None

                stats = {
                    "platform": "Jetson Thor (jtop)",
                    "gpu_name": self.gpu_name,
                    "board_name": board_name,  # Add board name
                    "gpu_percent": gpu_percent,
                    "vram_used_gb": vram_used_gb,
                    "vram_total_gb": vram_total_gb,
                    "vram_percent": vram_percent,
                    "temp_c": temp_c,
                    "power_w": power_w,
                    **system_stats,
                }

                # Update history
                self.update_history(stats)

                return stats

            except Exception as e:
                logger.error(f"Error reading jtop stats: {e}")
                logger.warning("Falling back to nvhost_podgov")
                self.use_jtop = False  # Disable jtop, try fallback

        # Fallback to nvhost_podgov (GPU util only, no VRAM)
        try:
            # Read GPC (Graphics Processing Cluster) load
            with open(self.gpc_load_target, "r") as f:
                gpc_load = int(f.read().strip())
            with open(self.gpc_load_max, "r") as f:
                gpc_max = int(f.read().strip())

            # Calculate GPU utilization percentage
            gpu_percent = (gpc_load / gpc_max * 100) if gpc_max > 0 else 0

            # Try to read NVD (NVIDIA Display) load as well
            try:
                with open(self.nvd_load_target, "r") as f:
                    nvd_load = int(f.read().strip())
                with open(self.nvd_load_max, "r") as f:
                    nvd_max = int(f.read().strip())
                nvd_percent = (nvd_load / nvd_max * 100) if nvd_max > 0 else 0

                # Use the maximum of GPC and NVD as overall GPU utilization
                gpu_percent = max(gpu_percent, nvd_percent)
            except Exception:

                pass  # NVD not critical, use GPC only

            stats = {
                "platform": "Jetson Thor (nvhost_podgov)",
                "gpu_name": self.gpu_name,
                "gpu_percent": gpu_percent,
                "vram_used_gb": 0,  # Not available via this method
                "vram_total_gb": 0,
                "vram_percent": 0,
                "temp_c": None,
                "power_w": None,
                **system_stats,
            }

            # Update history
            self.update_history(stats)

            return stats

        except Exception as e:
            logger.error(f"Error reading Thor GPU stats: {e}")
            self.available = False  # Disable further attempts
            return {
                "platform": "Jetson Thor (error)",
                "gpu_name": self.gpu_name,
                "gpu_percent": 0,
                "vram_used_gb": 0,
                "vram_total_gb": 0,
                "vram_percent": 0,
                **system_stats,
            }

    def cleanup(self):
        """Cleanup resources"""
        if self.use_jtop and self.jtop_instance:
            try:
                self.jtop_instance.close()
                logger.info("jtop closed successfully")
            except Exception as e:
                logger.error(f"Error closing jtop: {e}")


class AppleSiliconMonitor(GPUMonitor):
    """
    Apple Silicon GPU monitoring for M1/M2/M3/M4 chips

    Note: Ollama uses Metal (GPU cores), not the Neural Engine.
    The Neural Engine is used by Core ML, not llama.cpp-based inference.

    For detailed monitoring, install: brew install asitop
    Then run: sudo asitop
    """

    def __init__(self, history_size: int = 60):
        super().__init__(history_size)
        self.available = False
        self.gpu_name = "Apple GPU"
        self.chip_type = "Unknown"
        self.chip_variant = ""  # Pro, Max, Ultra, or empty for base
        self.gpu_cores = 0
        self.product_name = ""
        self.use_powermetrics = False
        self.powermetrics_warned = False

        # Check if running in Docker on Mac (host system info passed as env vars)
        if os.environ.get("DOCKER_ENV") == "mac" and os.environ.get("HOST_CPU_MODEL"):
            cpu_brand = os.environ.get("HOST_CPU_MODEL", "")
            self.product_name = os.environ.get("HOST_PRODUCT_NAME", "Mac")
            # Override hostname to show host's hostname
            import socket

            self._hostname = os.environ.get("HOST_HOSTNAME", socket.gethostname())

            # Extract chip type from host CPU model
            if "Apple" in cpu_brand:
                for chip in ["M4", "M3", "M2", "M1"]:
                    if chip in cpu_brand:
                        self.chip_type = chip
                        if "Ultra" in cpu_brand:
                            self.chip_variant = "Ultra"
                        elif "Max" in cpu_brand:
                            self.chip_variant = "Max"
                        elif "Pro" in cpu_brand:
                            self.chip_variant = "Pro"

                        if self.chip_variant:
                            self.gpu_name = f"{chip} {self.chip_variant}"
                        else:
                            self.gpu_name = chip
                        break

            logger.info(f"Apple Silicon detected (via Docker host): {cpu_brand}")
            logger.info(f"Host product: {self.product_name}")
            logger.info(f"Host hostname: {self._hostname}")
            self.available = True

            # Try to get GPU cores from host (if passed)
            # For now, skip detailed detection in Docker, will add if needed
        else:
            # Native macOS detection
            # Detect chip type and variant
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if result.returncode == 0:
                    cpu_brand = result.stdout.strip()
                    # Extract chip type (M1, M2, M3, M4, etc.)
                    if "Apple" in cpu_brand:
                        for chip in [
                            "M4",
                            "M3",
                            "M2",
                            "M1",
                        ]:  # Check in reverse order for correct match
                            if chip in cpu_brand:
                                self.chip_type = chip

                                # Extract variant (Pro, Max, Ultra)
                                if "Ultra" in cpu_brand:
                                    self.chip_variant = "Ultra"
                                elif "Max" in cpu_brand:
                                    self.chip_variant = "Max"
                                elif "Pro" in cpu_brand:
                                    self.chip_variant = "Pro"

                                # Build GPU name with variant
                                if self.chip_variant:
                                    self.gpu_name = f"{chip} {self.chip_variant}"
                                else:
                                    self.gpu_name = chip

                                break
                    logger.info(f"Apple Silicon detected: {cpu_brand}")
                    self.available = True
            except Exception as e:
                logger.warning(f"Failed to detect Apple Silicon: {e}")

            # Get product name (MacBook Pro 16", etc.)
            try:
                result = subprocess.run(
                    ["system_profiler", "SPHardwareDataType"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if result.returncode == 0:
                    for line in result.stdout.split("\n"):
                        if "Model Name:" in line:
                            # Extract "MacBook Pro" etc.
                            self.product_name = line.split(":")[1].strip()
                        elif "Model Identifier:" in line:
                            line.split(":")[1].strip()

                    # Try to get screen size from display info
                    if self.product_name and "MacBook" in self.product_name:
                        try:
                            display_result = subprocess.run(
                                ["system_profiler", "SPDisplaysDataType"],
                                capture_output=True,
                                text=True,
                                timeout=3,
                            )
                            if display_result.returncode == 0:
                                # Look for built-in display resolution to infer size
                                lines = display_result.stdout.split("\n")
                                for i, line in enumerate(lines):
                                    if "Built-In" in line or "Color LCD" in line:
                                        # Check next few lines for resolution
                                        for j in range(i, min(i + 10, len(lines))):
                                            if "Resolution:" in lines[j]:
                                                res = lines[j].lower()
                                                # Infer screen size from resolution
                                                if (
                                                    "3456" in res or "3024" in res
                                                ):  # 14" and 16" MacBook Pro
                                                    # Check if it's 16" (3456x2234) or 14" (3024x1964)
                                                    if "3456" in res:
                                                        self.product_name += ' 16"'
                                                    elif "3024" in res:
                                                        self.product_name += ' 14"'
                                                elif (
                                                    "2880" in res and "1800" in res
                                                ):  # 15" MacBook Air
                                                    self.product_name += ' 15"'
                                                elif (
                                                    "2560" in res and "1664" in res
                                                ):  # 13" MacBook Air/Pro
                                                    self.product_name += ' 13"'
                                                break
                                        break
                        except Exception as e:
                            logger.debug(f"Could not determine screen size: {e}")
            except Exception as e:
                logger.debug(f"Could not get product name: {e}")

            # Get actual GPU core count from system_profiler
            try:
                result = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if result.returncode == 0:
                    for line in result.stdout.split("\n"):
                        # Look for "Total Number of Cores:" in GPU section
                        if "Total Number of Cores:" in line or "Cores:" in line:
                            try:
                                cores_str = line.split(":")[1].strip()
                                self.gpu_cores = int(cores_str)
                                break
                            except Exception:

                                pass
            except Exception as e:
                logger.debug(f"Could not get GPU core count: {e}")

            # Check if powermetrics is available (requires sudo, so likely not usable)
            try:
                result = subprocess.run(["which", "powermetrics"], capture_output=True, timeout=1)
                if result.returncode == 0:
                    self.use_powermetrics = True
                    logger.info("powermetrics found - but requires sudo for GPU stats")
            except Exception:

                pass

        if self.available:
            logger.info("Apple Silicon monitoring initialized")
            if self.product_name:
                logger.info(f"Product: {self.product_name}")
            logger.info(
                f"Chip: {self.gpu_name} ({self.gpu_cores}-core GPU)"
                if self.gpu_cores > 0
                else f"Chip: {self.gpu_name}"
            )
            logger.info("💡 Ollama uses Metal (GPU) for inference, not Neural Engine")
            logger.info("💡 For detailed monitoring: brew install asitop && sudo asitop")
            logger.info("💡 Or use Activity Monitor > Window > GPU History")

    def get_cpu_ram_stats(self) -> Dict:
        """Get CPU and RAM stats, with custom hostname for Docker"""
        stats = super().get_cpu_ram_stats()
        # Override hostname if running in Docker on Mac
        if hasattr(self, "_hostname"):
            stats["hostname"] = self._hostname
        return stats

    def get_stats(self) -> Dict:
        """Get current system stats for Apple Silicon"""
        system_stats = self.get_cpu_ram_stats()

        if not self.available:
            return {
                "platform": "Apple Silicon (unavailable)",
                "gpu_name": "N/A",
                "gpu_percent": 0,
                "vram_used_gb": 0,
                "vram_total_gb": 0,
                "vram_percent": 0,
                **system_stats,
            }

        # Try to get GPU stats via powermetrics (requires sudo, so will likely fail)
        gpu_percent = None  # None = unavailable, will show as "N/A" in UI
        if self.use_powermetrics and not self.powermetrics_warned:
            try:
                # powermetrics requires sudo and is heavyweight
                # This will likely fail, but we try once
                result = subprocess.run(
                    ["powermetrics", "-n", "1", "-i", "100", "--samplers", "gpu_power"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0 and result.stdout:
                    # Parse GPU active residency if available
                    for line in result.stdout.split("\n"):
                        if "GPU active residency" in line:
                            # Extract percentage
                            try:
                                gpu_percent = float(line.split(":")[1].strip().rstrip("%"))
                            except Exception:

                                pass
            except subprocess.TimeoutExpired:
                if not self.powermetrics_warned:
                    logger.warning("powermetrics requires sudo - GPU utilization unavailable")
                    logger.info(
                        "💡 Install asitop for GPU monitoring: brew install asitop && sudo asitop"
                    )
                    self.powermetrics_warned = True
                    self.use_powermetrics = False
            except Exception as e:
                if not self.powermetrics_warned:
                    logger.warning(f"powermetrics not accessible: {e}")
                    self.powermetrics_warned = True
                    self.use_powermetrics = False

        # Apple Silicon uses unified memory (shared between CPU and GPU)
        stats = {
            "platform": f"Apple Silicon ({self.chip_type})",
            "gpu_name": self.gpu_name,
            "gpu_cores": self.gpu_cores,
            "product_name": self.product_name,
            "chip_variant": self.chip_variant,
            "gpu_percent": gpu_percent,  # Will be 0 without sudo powermetrics
            "vram_used_gb": system_stats["ram_used_gb"],  # Unified memory
            "vram_total_gb": system_stats["ram_total_gb"],  # Unified memory
            "vram_percent": system_stats["ram_percent"],
            "temp_c": None,  # Would need IOKit APIs or asitop
            "power_w": None,  # Would need sudo powermetrics
            **system_stats,
        }

        # Update history
        self.update_history(stats)

        return stats

    def cleanup(self):
        """Cleanup resources"""
        pass


class JetsonOrinMonitor(GPUMonitor):
    """Jetson Orin GPU monitoring using jtop (jetson_stats)"""

    def __init__(self, history_size: int = 60):
        super().__init__(history_size)
        self.gpu_name = "Jetson Orin"
        self.available = False
        self.use_jtop = False
        self.jtop_instance = None

        # Try jtop (best support for Orin - GPU, VRAM, temp, power)
        try:
            from jtop import jtop

            self.jtop_instance = jtop()
            self.jtop_instance.start()
            self.use_jtop = True
            self.available = True
            logger.info("Jetson Orin monitoring initialized - using jtop (jetson_stats)")
        except ImportError:
            logger.warning(
                "jtop (jetson_stats) not installed - install with: sudo pip3 install jetson-stats"
            )
        except Exception as e:
            logger.warning(f"jtop initialization failed: {e}")

    def get_stats(self) -> Dict:
        """Get current GPU stats for Jetson Orin"""
        system_stats = self.get_cpu_ram_stats()

        if not self.available:
            return {
                "platform": "Jetson Orin (monitoring unavailable)",
                "gpu_name": self.gpu_name,
                "gpu_percent": 0,
                "vram_used_gb": 0,
                "vram_total_gb": 0,
                "vram_percent": 0,
                **system_stats,
            }

        # Use jtop for stats
        if self.use_jtop and self.jtop_instance:
            try:
                # Get stats from jtop
                gpu_percent = self.jtop_instance.stats.get("GPU", 0)

                # Get memory stats (jtop uses shared memory on Jetson)
                memory = self.jtop_instance.memory
                # Orin uses unified memory, RAM is shared with GPU
                # jtop returns memory in KB, convert to GB (divide by 1024^2)
                vram_used_gb = memory.get("RAM", {}).get("used", 0) / (1024 * 1024)
                vram_total_gb = memory.get("RAM", {}).get("tot", 0) / (1024 * 1024)
                vram_percent = (vram_used_gb / vram_total_gb * 100) if vram_total_gb > 0 else 0

                # Temperature
                temp_c = None
                if hasattr(self.jtop_instance, "temperature"):
                    temps = self.jtop_instance.temperature
                    # Try to get GPU temp
                    temp_c = temps.get("GPU", temps.get("thermal", None))

                # Power
                power_w = None
                if hasattr(self.jtop_instance, "power"):
                    power = self.jtop_instance.power
                    # Sum all power rails if available
                    if isinstance(power, dict):
                        power_w = (
                            sum(p.get("power", 0) for p in power.values() if isinstance(p, dict))
                            / 1000
                        )  # mW to W

                # Get board name (e.g., "Jetson AGX Orin Developer Kit")
                board_name = None
                if hasattr(self.jtop_instance, "board"):
                    board_info = self.jtop_instance.board
                    if isinstance(board_info, dict):
                        # Try hardware.Model first, then other fields
                        if "hardware" in board_info and isinstance(board_info["hardware"], dict):
                            board_name = board_info["hardware"].get("Model") or board_info[
                                "hardware"
                            ].get("Module")
                        if (
                            not board_name
                            and "info" in board_info
                            and isinstance(board_info["info"], dict)
                        ):
                            board_name = board_info["info"].get("Machine") or board_info[
                                "info"
                            ].get("Model")
                        if not board_name and "platform" in board_info:
                            platform = board_info["platform"]
                            if isinstance(platform, dict):
                                board_name = platform.get("Machine")
                            elif isinstance(platform, str):
                                board_name = platform
                        if board_name and not isinstance(board_name, str):
                            logger.warning(
                                f"Board name is not a string: {type(board_name)}, value: {board_name}"
                            )
                            board_name = str(board_name) if board_name else None

                return {
                    "platform": "Jetson Orin (jtop)",
                    "gpu_name": self.gpu_name,
                    "gpu_percent": gpu_percent,
                    "vram_used_gb": round(vram_used_gb, 2),
                    "vram_total_gb": round(vram_total_gb, 2),
                    "vram_percent": round(vram_percent, 1),
                    "gpu_temp_c": temp_c,
                    "gpu_power_w": power_w,
                    "board_name": board_name,
                    **system_stats,
                }
            except Exception as e:
                logger.error(f"Error getting jtop stats: {e}")
                return {
                    "platform": "Jetson Orin (jtop error)",
                    "gpu_name": self.gpu_name,
                    "gpu_percent": 0,
                    "vram_used_gb": 0,
                    "vram_total_gb": 0,
                    "vram_percent": 0,
                    **system_stats,
                }

        return {
            "platform": "Jetson Orin (no stats)",
            "gpu_name": self.gpu_name,
            "gpu_percent": 0,
            "vram_used_gb": 0,
            "vram_total_gb": 0,
            "vram_percent": 0,
            **system_stats,
        }

    def cleanup(self):
        """Cleanup resources"""
        if self.use_jtop and self.jtop_instance:
            try:
                self.jtop_instance.close()
                logger.info("jtop closed successfully")
            except Exception as e:
                logger.warning(f"Error closing jtop: {e}")


def create_monitor(platform: Optional[str] = None) -> GPUMonitor:
    """
    Factory function to create appropriate GPU monitor

    Args:
        platform: Force specific platform ('nvidia', 'jetson_orin', 'jetson_thor', 'apple', etc.)
                 If None, auto-detect

    Returns:
        Appropriate GPUMonitor instance
    """
    # Force specific platform if requested
    if platform == "jetson_thor":
        return JetsonThorMonitor()
    if platform == "jetson_orin":
        return JetsonOrinMonitor()
    if platform == "apple" or platform == "apple_silicon":
        return AppleSiliconMonitor()

    # Auto-detect macOS / Apple Silicon
    import platform as platform_module

    if platform is None and platform_module.system() == "Darwin":
        logger.info("Auto-detected macOS - using AppleSiliconMonitor")
        return AppleSiliconMonitor()

    # Auto-detect Jetson Thor by checking for Thor-specific paths
    if platform is None:
        thor_gpc_path = "/sys/devices/platform/bus@0/d0b0000000.pcie/pci0000:00/0000:00:00.0/0000:01:00.0/gpu-gpc-0/devfreq/gpu-gpc-0/nvhost_podgov/load_target"
        try:
            if os.path.exists(thor_gpc_path):
                logger.info("Auto-detected Jetson Thor (nvhost_podgov paths found)")
                return JetsonThorMonitor()
        except Exception:

            pass

    # Try NVML (works for Desktop, DGX, some Jetsons)
    if platform == "nvidia" or platform is None:
        try:
            import pynvml

            pynvml.nvmlInit()
            # Check if it's Thor by GPU name
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            gpu_name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(gpu_name, bytes):
                gpu_name = gpu_name.decode("utf-8")
            pynvml.nvmlShutdown()

            # If Jetson (Thor or Orin) detected, use jtop-based monitor for better stats
            if "Thor" in gpu_name:
                logger.info(f"Detected {gpu_name} - using JetsonThorMonitor for jtop-based stats")
                return JetsonThorMonitor()
            elif "Orin" in gpu_name or "nvgpu" in gpu_name:
                logger.info(f"Detected {gpu_name} - using JetsonOrinMonitor for jtop-based stats")
                return JetsonOrinMonitor()

            logger.info("Auto-detected NVIDIA GPU (NVML available)")
            return NVMLMonitor()
        except Exception:

            pass

    # Fallback to NVML (will show unavailable)
    logger.warning("No GPU detected, using fallback monitor")
    return NVMLMonitor()
