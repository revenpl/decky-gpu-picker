# GPU Picker (Decky plugin)

![GPU Picker](screenshot.jpg)

GPU list + copies the ready-made game launch command to the clipboard:

    MESA_VK_DEVICE_SELECT="<vendor:device>!" %command%

e.g. `MESA_VK_DEVICE_SELECT="1002:13c0!" %command%` (iGPU) or
`MESA_VK_DEVICE_SELECT="1002:7550!" %command%` (RX 9070 XT).

**Why PCI id, not a name filter?** `MESA_VK_DEVICE_SELECT` is read by the
`VK_LAYER_MESA_device_select` Vulkan layer, which is *implicit* on Mesa/RADV
systems - so it auto-loads into every Vulkan app, including vkd3d-proton - and
selects the device by PCI id, **independent of the (often wrong) device name**.
The trailing `!` makes the selected device the only one visible to the app.

This is the robust selector for *all* cards, and the only one that works for a
nameless iGPU (e.g. `Radeon Graphics`, PCI `1002:13c0`). A name-based filter
(`VKD3D_FILTER_DEVICE_NAME`) is a case-insensitive substring match on the real
Vulkan device name, and the name the plugin holds may be an lspci *family*
string (`Radeon RX 9070/9070 XT/9070 GRE`) or a CPU name
(`AMD Ryzen 7 9800X3D ... (RADV RAPHAEL_MENDOCINO)`) that is **not** a substring
of the real Vulkan name - in which case the filter matches nothing and
vkd3d-proton crashes with no device. Selecting by PCI id avoids that whole class
of name-mismatch crashes.

Selection logic: the PCI id is the sole selector; the same name more than once
(two identical cards sharing a PCI id) -> the command additionally sets
`VKD3D_VULKAN_DEVICE=<index>` (0-based enumeration position) to pick the exact
device. Only when PCI is entirely missing (degenerate enumeration) does the
command fall back to the legacy model-token name filter.

Pattern: the GPU-selection fix for SteamOS (2026-09-21).
**Zero runtime dependencies** - GPU names are read straight from the Vulkan loader
(libvulkan.so.1, present on SteamOS); we install nothing. Fallback: lspci -> /sys.

## Installation
1. `./package.sh` (needs node + npm + python3) -> `decky-gpu-picker.zip`.
2. Move the zip to the machine with Decky.
3. Decky -> Plugins -> Install from zip -> enable "GPU Picker".

Dev alternative: copy the directory (with `dist/`) to `~/homebrew/plugins/decky-gpu-picker/`
and restart Decky.

## Usage
Open the plugin -> GPU list (`deviceName` from the Vulkan loader; fallback `lspci`; fallback /sys) ->
click a card -> the command lands in the clipboard -> paste it into the game's "Launch Options" in Steam.

## Known environment quirk: Decky Loader (SteamOS)

The Decky Loader is a PyInstaller bundle: it unpacks itself to a temp dir
(e.g. `/tmp/_MEI...`) containing its *own* copies of `libstdc++.so.6`,
`libz.so.1`, `libffi.so.8` and puts that dir in `LD_LIBRARY_PATH`. Inside the
plugin backend process, the Vulkan loader then dlopens the AMD ICD
(`libvulkan_radeon.so`), whose dependencies resolve to the bundle's copies ->
`VK_ERROR_INCOMPATIBLE_DRIVER` (`rc=-3`) -> zero devices -> the `lspci`
fallback (why the iGPU showed as `Phoenix1` on a Legion Go).

The dynamic linker's search path is fixed at process start, so in-process
fixes (e.g. editing `os.environ`) cannot repair it. The plugin handles this
with a fallback: if in-process enumeration yields no devices, it re-runs
enumeration in a clean child process with all `LD_*` variables stripped, so
the system driver libraries are resolved instead
(`vulkan.py`: `_subprocess_enumerate`). Stdlib only, recursion-guarded.

If the GPU list still looks wrong, the exact reasons are written to the first
writable location of `~/.gpu_diag.txt` / `/tmp/gpu_diag.txt` (the SteamOS
plugin dir is root-owned) and to the backend log.

## Development
    python3 -m unittest -v      # 23 tests (17 GPU logic + 6 Vulkan FFI/fallback)
    npm install && npm run build
    ./package.sh
