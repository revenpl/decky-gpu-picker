import decky

import gpus


class Plugin:
    async def _main(self):
        decky.logger.info("GPU Picker: start")

    async def _unload(self):
        decky.logger.info("GPU Picker: stop")

    async def list_gpus(self):
        """Called from the frontend as callable('list_gpus')."""
        return gpus.list_gpus()

    async def build_command(self, name: str, index: int | None = None) -> str:
        """Called from the frontend as callable('build_command', name, index).

        `index` = position of the clicked card in the GPU list. A unique name
        -> name filters only. A duplicated name (the same name appears more
        than once in the list) -> additionally VKD3D_VULKAN_DEVICE=<index>.
        """
        count = sum(1 for d in gpus.list_gpus() if d.get("name") == name)
        return gpus.build_command(name, index, name_count=count)
