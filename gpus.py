"""GPU detection and launch-command building (pure logic).

No Decky dependency and no ctypes - fully unit-testable.
Vulkan enumeration lives separately in `vulkan.py` (FFI) and is injected into list_gpus.
"""

import re
import glob
import os
import shutil
import subprocess

__all__ = [
    "build_command",
    "parse_lspci",
    "scan_sys",
    "list_gpus",
]


def build_command(name: str, index: int | None = None, name_count: int = 1) -> str:
    """Build the launch-option command for the chosen GPU.

    A unique name (name_count == 1) -> name filters only, the most stable
    selector. A duplicated name (name_count > 1, e.g. two identical cards) ->
    additionally VKD3D_VULKAN_DEVICE=<index> (the 0-based enumeration
    position; in vkd3d-proton the index wins over the name filter, which
    stays as a safety net).

    The VKD3D/DXVK filter is a substring match on the Vulkan device name, so
    the full deviceName is safe. Backslash and double quote are escaped so
    the name does not break the shell.
    """
    safe = name.replace("\\", "\\\\").replace('"', '\\"')
    filters = (
        f'VKD3D_FILTER_DEVICE_NAME="{safe}" '
        f'DXVK_FILTER_DEVICE_NAME="{safe}"'
    )
    if index is not None and name_count > 1:
        return f"{filters} VKD3D_VULKAN_DEVICE={index} %command%"
    return f"{filters} %command%"


def parse_lspci(text: str) -> list:
    """`VGA compatible controller [0300]` lines from `lspci -nn` -> {"name", "pci"}."""
    devices = []
    for line in text.splitlines():
        if "VGA compatible controller [0300]" not in line:
            continue
        brackets = re.findall(r"\[([^\]]+)\]", line)
        pci_m = re.search(r"\[([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\]", line)
        name = next((b for b in brackets if " " in b), None)
        devices.append({"name": name, "pci": pci_m.group(1) if pci_m else None})
    return devices
