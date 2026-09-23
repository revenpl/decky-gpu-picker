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

    The backend (Nuitka, run as root by systemd) has a different environment
    than a normal shell, so Vulkan / lspci can fail there even when they work
    elsewhere. Writing the real reasons to a file (instead of logging them,
    which would only be visible in a root-owned journal) is what lets us see
    the actual cause on the device.
    """
    if not result or result[0].get("source") == "vulkan":
        return
    try:
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
        with open(_DIAG_PATH, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        decky.logger.warning("GPU Picker: Vulkan list failed; diagnostic written to %s", _DIAG_PATH)
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

    async def build_command(self, name: str, index: int | None = None) -> str:
        """Called from the frontend as callable('build_command', name, index).

        `index` = position of the clicked card in the GPU list. A unique name
        -> name filters only. A duplicated name (the same name appears more
        than once in the list) -> additionally VKD3D_VULKAN_DEVICE=<index>.
        """
        count = sum(1 for d in gpus.list_gpus() if d.get("name") == name)
        return gpus.build_command(name, index, name_count=count)
