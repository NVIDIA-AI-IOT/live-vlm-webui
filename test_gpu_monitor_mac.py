#!/usr/bin/env python3
"""
Quick test script for GPU monitor on Mac
Run this to verify CPU/RAM stats work correctly
"""

import asyncio
import logging
from gpu_monitor import create_monitor

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def test_monitor():
    """Test GPU monitor functionality"""
    print("=" * 60)
    print("GPU Monitor Test - Mac Compatibility")
    print("=" * 60)

    # Create monitor (will auto-detect platform)
    monitor = create_monitor()

    # Get stats
    print("\nRetrieving system stats...")
    stats = monitor.get_stats()

    # Display results
    print("\n" + "=" * 60)
    print("System Information:")
    print("=" * 60)
    print(f"Platform:      {stats.get('platform', 'N/A')}")
    print(f"Product Name:  {stats.get('product_name', 'N/A')}")
    print(f"CPU Model:     {stats.get('cpu_model', 'N/A')}")
    print(f"GPU Name:      {stats.get('gpu_name', 'N/A')}")
    print(f"GPU Cores:     {stats.get('gpu_cores', 0)}")
    print(f"Hostname:      {stats.get('hostname', 'N/A')}")

    print("\n" + "=" * 60)
    print("CPU & RAM Stats:")
    print("=" * 60)
    print(f"CPU Usage:     {stats.get('cpu_percent', 0):.1f}%")
    print(f"RAM Used:      {stats.get('ram_used_gb', 0):.2f} GB")
    print(f"RAM Total:     {stats.get('ram_total_gb', 0):.2f} GB")
    print(f"RAM Usage:     {stats.get('ram_percent', 0):.1f}%")

    print("\n" + "=" * 60)
    print("GPU Stats:")
    print("=" * 60)
    print(f"GPU Usage:     {stats.get('gpu_percent', 0):.1f}%")
    print(f"VRAM Used:     {stats.get('vram_used_gb', 0):.2f} GB")
    print(f"VRAM Total:    {stats.get('vram_total_gb', 0):.2f} GB")
    print(f"VRAM Usage:    {stats.get('vram_percent', 0):.1f}%")
    print(f"Temperature:   {stats.get('temp_c', 'N/A')}")
    print(f"Power:         {stats.get('power_w', 'N/A')}")

    print("\n" + "=" * 60)
    print("Test Results:")
    print("=" * 60)

    # Check what's working
    working = []
    not_working = []

    if stats.get('cpu_model', 'Unknown CPU') != 'Unknown CPU':
        working.append("✅ CPU detection")
    else:
        not_working.append("❌ CPU detection")

    if stats.get('cpu_percent', 0) > 0:
        working.append("✅ CPU usage monitoring")
    else:
        not_working.append("❌ CPU usage monitoring")

    if stats.get('ram_total_gb', 0) > 0:
        working.append("✅ RAM monitoring")
    else:
        not_working.append("❌ RAM monitoring")

    if stats.get('gpu_percent', 0) > 0 or 'unavailable' not in stats.get('platform', '').lower():
        working.append("✅ GPU monitoring")
    else:
        not_working.append("❌ GPU monitoring (expected on Mac without Apple Silicon support)")

    for item in working:
        print(item)
    for item in not_working:
        print(item)

    print("\n" + "=" * 60)
    print("Conclusion:")
    print("=" * 60)
    if "NVIDIA" in stats.get('platform', '') and 'unavailable' in stats.get('platform', '').lower():
        print("✅ Basic monitoring works (CPU/RAM)")
        print("⚠️  GPU monitoring unavailable (need Apple Silicon implementation)")
        print("\nOn Mac, you should see:")
        print("  - CPU model (Intel or Apple Silicon)")
        print("  - CPU usage percentage")
        print("  - RAM usage stats")
        print("  - GPU shows as 'unavailable' (expected)")

    # Cleanup
    monitor.cleanup()
    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(test_monitor())

