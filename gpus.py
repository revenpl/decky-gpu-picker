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


_MODEL_RE = re.compile(r"\b(\d{3,4})([A-Za-z0-9]{1,2})?")


def _extract_model_token(name: str) -> str:
    """Extract a short model token from a GPU name for the VKD3D/DXVK filter.

    VKD3D_FILTER_DEVICE_NAME / DXVK_FILTER_DEVICE_NAME are case-insensitive
    substring matches on the *Vulkan* device name (e.g. "AMD Radeon RX 9070 XT
    (RADV GFX1201)"). The lspci fallback, however, reports a *family* string
    such as "Radeon RX 9070/9070 XT/9070 GRE", which is NOT a substring of any
    concrete Vulkan device name, so a command built from the raw family string
    never matches.

    A short model token (the 3-4 digit model number plus any immediately
    following variant letters, e.g. "9070", "7900", "580") IS a substring of
    the Vulkan device name and of the lspci family string alike, so it works
    regardless of which source produced the name. When no digit sequence is
    found (e.g. "Radeon Graphics" on an iGPU, or "Phoenix1" on a Legion Go),
    the original name is returned unchanged.
    """
    m = _MODEL_RE.search(name)
    if m:
        return m.group(1) + (m.group(2) or "")
    return name


def _esc(value: str) -> str:
    """Escape a value for safe inclusion inside a double-quoted shell word."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_command(name: str, pci: str | None = None,
                  index: int | None = None, name_count: int = 1) -> str:
    """Build the launch-option command that forces the chosen GPU.

    PRIMARY selector (whenever a PCI id is known):
        MESA_VK_DEVICE_SELECT="<pci>!"
    The VK_LAYER_MESA_device_select layer is *implicit* on Mesa/RADV systems,
    so it auto-loads into every Vulkan application - including vkd3d-proton
    - and selects the device by PCI id, INDEPENDENT of the (often wrong)
    device NAME. The trailing "!" makes the selected device the only one
    visible to the app. This is what makes a nameless iGPU (e.g. "Radeon
    Graphics", PCI 1002:13c0) selectable at all: a name-based filter can never
    match such a device and vkd3d-proton would crash with no device.

    A duplicated name (name_count > 1, two identical cards sharing a PCI id)
    additionally gets VKD3D_VULKAN_DEVICE=<index> (0-based enumeration
    position) to pick the exact instance.

    NO name filter is emitted when PCI is known: a name-based filter (VKD3D/
    DXVK_FILTER_DEVICE_NAME) is a case-insensitive substring match on the
    *Vulkan* device name, and the name the plugin holds may be an lspci family
    string or a CPU name that is NOT a substring of the real Vulkan name
    (e.g. "9800X3" is not a substring of "AMD Ryzen 7 9800X3D ... (RADV ...)"),
    in which case it would zero out the device list and crash. The PCI select
    is the authoritative, verified selector - adding a name filter only
    introduces that crash risk with no benefit.

    Only when PCI is entirely missing (degenerate enumeration) do we fall back
    to the legacy model-token name filter.
    """
    parts: list[str] = []

    if pci:
        parts.append(f'MESA_VK_DEVICE_SELECT="{_esc(pci)}!"')
        if index is not None and name_count > 1:
            parts.append(f'VKD3D_VULKAN_DEVICE={index}')
    else:
        token = _extract_model_token(name)
        parts.append(f'VKD3D_FILTER_DEVICE_NAME="{_esc(token)}"')
        parts.append(f'DXVK_FILTER_DEVICE_NAME="{_esc(token)}"')
        if index is not None and name_count > 1:
            parts.append(f'VKD3D_VULKAN_DEVICE={index}')

    return " ".join(parts) + " %command%"


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
