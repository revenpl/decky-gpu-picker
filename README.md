# GPU Picker (Decky plugin)

![GPU Picker](screenshot.jpg)

> **Not in the official Decky Plugin Store — on purpose.**
> It was submitted to [SteamDeckHomebrew/decky-plugin-database](https://github.com/SteamDeckHomebrew/decky-plugin-database) (PR #1142, October 2026) and declined by the maintainers: the store's policy rejects plugins developed with extensive use of generative AI. The code here is fully open source (BSD-3-Clause), unit-tested (23 tests) and verified on two real devices across the Stable and Beta SteamOS channels — install it manually below.

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
1. In Decky: **Settings** (gear) → **General** → turn on **Developer mode**.
2. A new **Developer** section appears in the main Decky menu — open it.
3. Pick one:
   - **Install from URL** → paste:
     `https://github.com/revenpl/decky-gpu-picker/releases/download/v0.2.1/decky-gpu-picker.zip`
   - **Install from zip** → first download the zip from
     [GitHub Releases](https://github.com/revenpl/decky-gpu-picker/releases)
     (e.g. `wget -O decky-gpu-picker.zip "https://github.com/revenpl/decky-gpu-picker/releases/download/v0.2.1/decky-gpu-picker.zip"`),
     then select that file in Decky.
4. **Enable** "GPU Picker" in the plugin list. Done.

<details>
<summary>Building from source (developers)</summary>

1. `npm install && npm run build` (needs node + npm + python3)
2. `./package.sh` → `decky-gpu-picker.zip`
3. Install the zip as above (Decky → Developer → Install from zip).

Dev shortcut: copy the directory (with `dist/`) to `~/homebrew/plugins/decky-gpu-picker/`
and restart Decky.
</details>

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
