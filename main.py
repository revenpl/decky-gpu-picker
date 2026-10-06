import os
import sys

# Decky loads main.py in isolation (the plugin dir is NOT on sys.path), so make
# sibling modules (gpus.py, vulkan.py) importable before anything else.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import decky  # noqa: E402

import gpus  # noqa: E402
import vulkan  # noqa: E402

_DIAG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gpu_diag.txt")


def _write_diagnostic(result):
    """When the GPU list did not come from Vulkan, record WHY.

    The backend (Nuitka) runs with a different environment than a normal shell,
    so Vulkan / lspci can fail there even when they work elsewhere. On SteamOS
    the plugin directory is root-owned and the backend (user `deck`) cannot
    write into it, so the diagnostic is written to the FIRST writable location
    among: plugin dir -> ~/.gpu_diag.txt -> /tmp/gpu_diag.txt, and ALWAYS to
    the backend log (decky.logger) as a last resort.
    """
    if not result or result[0].get("source") == "vulkan":
        return
    lines = [
        f"fallback to: {result[0].get('source')}",
        f"vulkan.last_error: {vulkan.last_error!r}",
        f"gpus.last_lspci_error: {gpus.last_lspci_error!r}",
        f"LD_LIBRARY_PATH: {os.environ.get('LD_LIBRARY_PATH')!r}",
        f"VULKAN_LOADER_DRIVERS_PATH: {os.environ.get('VULKAN_LOADER_DRIVERS_PATH')!r}",
        f"VULKAN_ICD_FILENAMES: {os.environ.get('VULKAN_ICD_FILENAMES')!r}",
        f"PATH: {os.environ.get('PATH')!r}",
        f"result: {result!r}",
    ]
    text = "\n".join(lines) + "\n"

    candidates = [
        _DIAG_PATH,
        os.path.join(os.path.expanduser("~"), ".gpu_diag.txt"),
        "/tmp/gpu_diag.txt",
    ]
    written_to = None
    for path in candidates:
        try:
            with open(path, "w") as fh:
                fh.write(text)
            written_to = path
            break
        except OSError:
            continue

    try:
        if written_to:
            decky.logger.warning("GPU Picker: Vulkan list failed; diagnostic -> %s", written_to)
        else:
            decky.logger.warning("GPU Picker: Vulkan list failed; no writable diag path. %s", text)
    except Exception:
        pass


class Plugin:
    async def _main(self):
        decky.logger.info("GPU Picker: start")

    async def _unload(self):
        decky.logger.info("GPU Picker: stop")

    async def list_gpus(self):
        """Called from the frontend as callable('list_gpus')."""
        result = gpus.list_gpus()
        _write_diagnostic(result)
        return result

    async def build_command(self, name: str, index: int | None = None,
                            pci: str | None = None) -> str:
        """Called from the frontend as callable('build_command', name, index, pci).

        `pci` = the PCI id of the clicked card - the stable, name-independent
        device identity, and the PRIMARY selector (MESA_VK_DEVICE_SELECT). This
        is what makes a nameless iGPU (e.g. "Radeon Graphics", PCI 1002:13c0)
        selectable, where a name-based filter can never match.
        `index` = position of the clicked card in the GPU list; a duplicated
        name (the same name appears more than once) -> additionally
        VKD3D_VULKAN_DEVICE=<index> to pick the exact instance.
        """
        count = sum(1 for d in gpus.list_gpus() if d.get("name") == name)
        return gpus.build_command(name, pci=pci, index=index, name_count=count)
