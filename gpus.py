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
    "last_lspci_error",
]

# Reason the lspci fallback failed ("" when it was not reached or succeeded),
# for diagnostics.
last_lspci_error = ""


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


def scan_sys() -> list:
    """Last fallback: GPUs from /sys/class/drm (PCI ID only, no names)."""
    out = []
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        vendor_f = os.path.join(card, "device", "vendor")
        device_f = os.path.join(card, "device", "device")
        try:
            vendor = int(open(vendor_f).read().strip(), 16)
            device = int(open(device_f).read().strip(), 16)
        except (OSError, ValueError):
            continue
        out.append({"name": None, "pci": f"{vendor:04x}:{device:04x}"})
    return out


def _normalize(d: dict, source: str, index: int) -> dict:
    return {
        "name": d.get("name") or d.get("pci") or "unknown",
        "pci": d.get("pci"),
        "source": source,
        "index": index,  # 0-based position (used for VKD3D_VULKAN_DEVICE)
    }


def _run_tool(run, cmd):
    """Run a fallback CLI tool; record why it failed for diagnostics."""
    global last_lspci_error
    last_lspci_error = ""
    try:
        p = run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        last_lspci_error = f"{cmd[0]} raised {exc!r}"
        return None
    if p.returncode != 0:
        last_lspci_error = f"{cmd[0]} rc={p.returncode} stderr={p.stderr.strip()[:200]}"
        return None
    return p.stdout


def list_gpus(vulkan_devices=None, lspci_run=subprocess.run,
              which=shutil.which, sys_gpus=None):
    """GPU list: Vulkan (ctypes, zero-dep) -> lspci -> /sys.

    `vulkan_devices` - a callable returning [{"name","pci"}]; defaults to
    `vulkan.enumerate_vulkan_devices` (lazy import so tests need no loader).
    `lspci_run`/`which`/`sys_gpus` are injectable for tests.
    Returns a list of {"name", "pci", "source", "index"} (index = 0-based position).
    """
    if vulkan_devices is None:
        from vulkan import enumerate_vulkan_devices as vulkan_devices
    devices = vulkan_devices()
    if devices:
        return [_normalize(d, "vulkan", i) for i, d in enumerate(devices)]

    global last_lspci_error
    if which and which("lspci"):
        out = _run_tool(lspci_run, ["lspci", "-nn"])
        if out:
            parsed = parse_lspci(out)
            if parsed:
                return [_normalize(d, "lspci", i) for i, d in enumerate(parsed)]
    else:
        last_lspci_error = "lspci not found (shutil.which returned None)"

    devices = sys_gpus if sys_gpus is not None else scan_sys()
    return [_normalize(d, "sys", i) for i, d in enumerate(devices)]
