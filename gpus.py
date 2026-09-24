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


def _extract_model_token(name: str) -> str:
    """Extract a short model token from a GPU name for the VKD3D/DXVK filter.

    VKD3D_FILTER_DEVICE_NAME / DXVK_FILTER_DEVICE_NAME are case-insensitive
    substring matches on the *Vulkan* device name (e.g. "AMD Radeon RX 9070 XT
    (RADV GFX1201)"). The lspci fallback, however, reports a *family* string
    such as "Radeon RX 9070/9070 XT/9070 GRE", which is NOT a substring of any
    concrete Vulkan device name, so a command built from it never matches.

    A short model token (the 3-4 digit model number plus any immediately
    following variant letters, e.g. "9070", "7900", "580") IS a substring of
    the Vulkan device name and of the lspci family string alike, so it works
    regardless of which source produced the name. When no digit sequence is
    found (e.g. "Phoenix1" on a Legion Go), fall back to the original name.
    """
    m = re.search(r"\b(\d{3,4})([A-Za-z0-9]{1,2})?", name)
    if m:
        return m.group(1) + (m.group(2) or "")
    return name


def build_command(name: str, index: int | None = None, name_count: int = 1) -> str:
    """Build the launch-option command for the chosen GPU.

    A unique name (name_count == 1) -> name filters only, the most stable
    selector. A duplicated name (name_count > 1, e.g. two identical cards) ->
    additionally VKD3D_VULKAN_DEVICE=<index> (the 0-based enumeration
    position; in vkd3d-proton the index wins over the name filter, which
    stays as a safety net).

    The filter value is a short model token (see _extract_model_token) so the
    command works whether the name came from the Vulkan path or the lspci
    family-string fallback. Backslash and double quote are escaped so the
    token does not break the shell.
    """
    token = _extract_model_token(name)
    safe = token.replace("\\", "\\\\").replace('"', '\\"')
    filters = (
        f'VKD3D_FILTER_DEVICE_NAME="{safe}" '
        f'DXVK_FILTER_DEVICE_NAME="{safe}"'
    )
    if index is not None and name_count > 1:
        return f"{filters} VKD3D_VULKAN_DEVICE={index} %command%"
    return f"{filters} %command%"


def parse_lspci(text: str) -> list:
    """`VGA compatible controller [0300]` lines from `lspci -nn` -> {"name", "pci"}.

    lspci -nn format (device line, not the class prefix):
        00:02.0 VGA compatible controller [0300]:
            Advanced Micro Devices, Inc. [AMD/ATI] Phoenix1 [1002:15bf]
    The PCI id is the first bracketed vendor:device token; the device name is
    the bare text between the vendor bracket and the PCI bracket (or, when
    absent, the bracket with a space - some drivers print it that way).
    A trailing "(rev c0)" must not be picked up as the name.
    """
    devices = []
    for line in text.splitlines():
        if "VGA compatible controller [0300]" not in line:
            continue
        pci_m = re.search(r"\[([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\]", line)
        pci = pci_m.group(1) if pci_m else None
        rest = line[:pci_m.start()] if pci_m else line
        rest = re.sub(r"\(.*?\)\s*$", "", rest)  # drop trailing "(rev xx)"
        name = None
        # Vendor bracket followed by a bare device name at the end:
        #   "Advanced Micro Devices, Inc. [AMD/ATI] Phoenix1"
        m = re.search(r"\[[^\]]*\]\s+([^\[\]()]+?)\s*$", rest)
        if m:
            name = m.group(1).strip()
        if not name:
            # No separate device token: use the last bracket that contains a
            # space (vendor names contain spaces, PCI IDs do not).
            m = re.search(r"\[([^\]]* [^\]]*)\]\s*$", rest)
            if m:
                name = m.group(1)
        devices.append({"name": name, "pci": pci})
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
