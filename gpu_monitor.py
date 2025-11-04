"""
GPU Monitoring Module
Supports multiple platforms: NVIDIA (NVML), Jetson Thor, Jetson Orin (tegrastats), Apple Silicon, AMD
"""
import asyncio
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
            except:
                pass

        elif system == "Darwin":  # macOS
            # Use sysctl to get CPU brand string
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            except:
                pass

        elif system == "Windows":
            # Use WMIC
            try:
                result = subprocess.run(
                    ["wmic", "cpu", "get", "name"],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split("\n")
                    if len(lines) > 1:
                        return lines[1].strip()
            except:
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
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            hostname = socket.gethostname()
            cpu_model = get_cpu_model()

            return {
                "cpu_percent": cpu_percent,
                "cpu_model": cpu_model,
                "ram_used_gb": memory.used / (1024**3),
                "ram_total_gb": memory.total / (1024**3),
                "ram_percent": memory.percent,
                "hostname": hostname
            }
        except Exception as e:
            logger.error(f"Error getting CPU/RAM stats: {e}")
            return {
                "cpu_percent": 0,
                "cpu_model": "Unknown CPU",
                "ram_used_gb": 0,
                "ram_total_gb": 0,
                "ram_percent": 0,
                "hostname": "Unknown"
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
            "ram_used": list(self.ram_used_history)
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

        try:
            import pynvml
            pynvml.nvmlInit()
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
            self.device_name = pynvml.nvmlDeviceGetName(self.handle)
            if isinstance(self.device_name, bytes):
                self.device_name = self.device_name.decode('utf-8')
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

            # Get memory info
            memory_info = pynvml.nvmlDeviceGetMemoryInfo(self.handle)
            vram_used_gb = memory_info.used / (1024**3)
            vram_total_gb = memory_info.total / (1024**3)
            vram_percent = (memory_info.used / memory_info.total) * 100

            # Get temperature
            try:
                temp = pynvml.nvmlDeviceGetTemperature(self.handle, pynvml.NVML_TEMPERATURE_GPU)
            except:
                temp = None

            # Get power usage
            try:
                power_mw = pynvml.nvmlDeviceGetPowerUsage(self.handle)
                power_w = power_mw / 1000.0
            except:
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
                **system_stats
            }

            # Update history
            self.update_history(stats)

            return stats

        except Exception as e:
            self.consecutive_errors += 1

            # Only log error once, or every 60 seconds (60 calls at 1Hz)
            if not self.error_logged:
                logger.error(f"Error getting NVML stats: {e}")
                logger.warning(f"GPU monitoring disabled - falling back to CPU/RAM only")
                self.error_logged = True
                self.available = False  # Don't try again
            elif self.consecutive_errors % 60 == 0:
                logger.warning(f"NVML still unavailable ({self.consecutive_errors} consecutive errors)")

            return self._get_fallback_stats()

    def _get_fallback_stats(self) -> Dict:
        """Fallback stats when GPU not available"""
        system_stats = self.get_cpu_ram_stats()

        # Use GPU name if we got it during init, otherwise show unavailable
        gpu_name = getattr(self, 'device_name', 'N/A')
        platform_name = f"NVIDIA {gpu_name} (monitoring unavailable)" if gpu_name != "N/A" else "NVIDIA (NVML unavailable)"

        return {
            "platform": platform_name,
            "gpu_name": gpu_name,
            "gpu_percent": 0,
            "vram_used_gb": 0,
            "vram_total_gb": 0,
            "vram_percent": 0,
            "temp_c": None,
            "power_w": None,
            **system_stats
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
            logger.info(f"Jetson Thor monitoring initialized - using jtop (jetson_stats)")
        except ImportError:
            logger.warning("jtop (jetson_stats) not installed - install with: sudo pip3 install jetson-stats")
        except Exception as e:
            logger.warning(f"jtop initialization failed: {e}")

        # Fallback to nvhost_podgov if jtop not available
        if not self.use_jtop:
            # Thor-specific paths (JetPack 7 / L4T r38.2)
            self.gpu_base_path = "/sys/devices/platform/bus@0/d0b0000000.pcie/pci0000:00/0000:00:00.0/0000:01:00.0"
            self.gpc_load_target = f"{self.gpu_base_path}/gpu-gpc-0/devfreq/gpu-gpc-0/nvhost_podgov/load_target"
            self.gpc_load_max = f"{self.gpu_base_path}/gpu-gpc-0/devfreq/gpu-gpc-0/nvhost_podgov/load_max"
            self.nvd_load_target = f"{self.gpu_base_path}/gpu-nvd-0/devfreq/gpu-nvd-0/nvhost_podgov/load_target"
            self.nvd_load_max = f"{self.gpu_base_path}/gpu-nvd-0/devfreq/gpu-nvd-0/nvhost_podgov/load_max"

            # Check if monitoring is available
            try:
                with open(self.gpc_load_target, 'r') as f:
                    f.read()
                self.available = True
                logger.info(f"Jetson Thor monitoring initialized - using nvhost_podgov (limited stats)")
                logger.info(f"💡 For full stats (GPU, VRAM, temp), install: sudo pip3 install jetson-stats")
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
                **system_stats
            }

        # Use jtop if available (full stats)
        if self.use_jtop and self.jtop_instance:
            try:
                # Get stats from jtop
                gpu_percent = self.jtop_instance.stats.get('GPU', 0)

                # Get memory stats (jtop uses shared memory on Jetson)
                memory = self.jtop_instance.memory
                # Thor uses unified memory, RAM is shared with GPU
                vram_used_gb = memory.get('RAM', {}).get('used', 0) / 1024  # Convert MB to GB
                vram_total_gb = memory.get('RAM', {}).get('tot', 0) / 1024
                vram_percent = (vram_used_gb / vram_total_gb * 100) if vram_total_gb > 0 else 0

                # Temperature
                temp_c = None
                if hasattr(self.jtop_instance, 'temperature'):
                    temps = self.jtop_instance.temperature
                    # Try to get GPU temp
                    temp_c = temps.get('GPU', temps.get('thermal', None))

                # Power
                power_w = None
                if hasattr(self.jtop_instance, 'power'):
                    power = self.jtop_instance.power
                    # Sum all power rails if available
                    if isinstance(power, dict):
                        power_w = sum(p.get('power', 0) for p in power.values() if isinstance(p, dict)) / 1000  # mW to W

                stats = {
                    "platform": "Jetson Thor (jtop)",
                    "gpu_name": self.gpu_name,
                    "gpu_percent": gpu_percent,
                    "vram_used_gb": vram_used_gb,
                    "vram_total_gb": vram_total_gb,
                    "vram_percent": vram_percent,
                    "temp_c": temp_c,
                    "power_w": power_w,
                    **system_stats
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
            with open(self.gpc_load_target, 'r') as f:
                gpc_load = int(f.read().strip())
            with open(self.gpc_load_max, 'r') as f:
                gpc_max = int(f.read().strip())

            # Calculate GPU utilization percentage
            gpu_percent = (gpc_load / gpc_max * 100) if gpc_max > 0 else 0

            # Try to read NVD (NVIDIA Display) load as well
            try:
                with open(self.nvd_load_target, 'r') as f:
                    nvd_load = int(f.read().strip())
                with open(self.nvd_load_max, 'r') as f:
                    nvd_max = int(f.read().strip())
                nvd_percent = (nvd_load / nvd_max * 100) if nvd_max > 0 else 0

                # Use the maximum of GPC and NVD as overall GPU utilization
                gpu_percent = max(gpu_percent, nvd_percent)
            except:
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
                **system_stats
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
                **system_stats
            }

    def cleanup(self):
        """Cleanup resources"""
        if self.use_jtop and self.jtop_instance:
            try:
                self.jtop_instance.close()
                logger.info("jtop closed successfully")
            except Exception as e:
                logger.error(f"Error closing jtop: {e}")


class JetsonOrinMonitor(GPUMonitor):
    """Jetson Orin GPU monitoring using tegrastats or /proc"""

    def __init__(self, history_size: int = 60):
        super().__init__(history_size)
        # TODO: Implement Jetson Orin monitoring
        logger.info("Jetson Orin monitoring not yet implemented")

    def get_stats(self) -> Dict:
        """Get current GPU stats for Jetson Orin"""
        # TODO: Parse tegrastats or /proc data
        system_stats = self.get_cpu_ram_stats()
        return {
            "platform": "Jetson Orin (tegrastats)",
            "gpu_name": "Jetson Orin",
            "gpu_percent": 0,
            "vram_used_gb": 0,
            "vram_total_gb": 0,
            "vram_percent": 0,
            **system_stats
        }

    def cleanup(self):
        """Cleanup resources"""
        pass


def create_monitor(platform: Optional[str] = None) -> GPUMonitor:
    """
    Factory function to create appropriate GPU monitor

    Args:
        platform: Force specific platform ('nvidia', 'jetson_orin', 'jetson_thor', etc.)
                 If None, auto-detect

    Returns:
        Appropriate GPUMonitor instance
    """
    # Force specific platform if requested
    if platform == "jetson_thor":
        return JetsonThorMonitor()
    if platform == "jetson_orin":
        return JetsonOrinMonitor()

    # Auto-detect Jetson Thor by checking for Thor-specific paths
    if platform is None:
        thor_gpc_path = "/sys/devices/platform/bus@0/d0b0000000.pcie/pci0000:00/0000:00:00.0/0000:01:00.0/gpu-gpc-0/devfreq/gpu-gpc-0/nvhost_podgov/load_target"
        try:
            if os.path.exists(thor_gpc_path):
                logger.info("Auto-detected Jetson Thor (nvhost_podgov paths found)")
                return JetsonThorMonitor()
        except:
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
                gpu_name = gpu_name.decode('utf-8')
            pynvml.nvmlShutdown()

            # If Thor detected, use JetsonThorMonitor for better stats
            if "Thor" in gpu_name:
                logger.info(f"Detected {gpu_name} - using JetsonThorMonitor for better stats")
                return JetsonThorMonitor()

            logger.info("Auto-detected NVIDIA GPU (NVML available)")
            return NVMLMonitor()
        except:
            pass

    # Fallback to NVML (will show unavailable)
    logger.warning("No GPU detected, using fallback monitor")
    return NVMLMonitor()

