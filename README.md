# GPU Picker (Decky plugin)

GPU list + copies the ready-made game launch command to the clipboard:

    VKD3D_FILTER_DEVICE_NAME="<model>" DXVK_FILTER_DEVICE_NAME="<model>" %command%

where `<model>` is a short model token (e.g. `9070`, `7900`, `580`).

**Why a token, not the full name?** `VKD3D_FILTER_DEVICE_NAME` /
`DXVK_FILTER_DEVICE_NAME` are case-insensitive *substring* matches on the real
Vulkan device name (e.g. `AMD Radeon RX 9070 XT (RADV GFX1201)`). The lspci
fallback, however, reports a *family* string such as `Radeon RX 9070/9070 XT/9070
GRE`, which is **not** a substring of any concrete Vulkan device name — so a
command built from the raw name never matches. A short model token is a
substring of the Vulkan name *and* of the lspci family string alike, so the
command works whether the name came from the Vulkan path or the lspci fallback.

Selection logic: a unique GPU name -> the command filters by the model token;
the same name more than once (two identical cards) -> the command additionally
sets `VKD3D_VULKAN_DEVICE=<index>` (0-based enumeration position) to pick the
exact device (in vkd3d-proton the index wins over the name filter, which stays
as a safety net).

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

## Development
    python3 -m unittest -v      # 15 tests (12 GPU logic + 3 FFI contract)
    npm install && npm run build
    ./package.sh
