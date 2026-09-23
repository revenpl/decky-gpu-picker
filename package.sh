#!/usr/bin/env bash
# Builds the frontend and packages the plugin into decky-gpu-picker.zip (installable by Decky).
#
# Zip layout (required by Decky, browser.py::_install + loader.py):
#   decky-gpu-picker/plugin.json   <- plugin.json at exactly depth 1 (file.count("/") == 1)
#   decky-gpu-picker/main.py
#   decky-gpu-picker/gpus.py
#   decky-gpu-picker/vulkan.py
#   decky-gpu-picker/README.md
#   decky-gpu-picker/LICENSE
#   decky-gpu-picker/dist/index.js <- loader reads <plugin>/dist/index.js
#
# NOTE: `python3 -m zipfile -c` stores entries by basename (flattens dist/), so we
# build the zip with the stdlib zipfile module using explicit arcnames.
set -euo pipefail
cd "$(dirname "$0")"

TOP="decky-gpu-picker"

rm -rf dist package decky-gpu-picker.zip
npm run build

python3 - "$TOP" <<'PY'
import pathlib
import sys
import zipfile

top = sys.argv[1]
entries = [
    "plugin.json",
    "main.py",
    "gpus.py",
    "vulkan.py",
    "README.md",
    "LICENSE",
    "dist/index.js",
]

# Source tree: build in ./dist, sources in the repo root.
root = pathlib.Path(".")
out = pathlib.Path(f"decky-gpu-picker.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in entries:
        src = root / name
        if not src.is_file():
            sys.exit(f"missing source file: {name}")
        z.write(src, arcname=f"{top}/{name}")

# Hard verification (Decky requirements):
with zipfile.ZipFile(out) as z:
    names = z.namelist()
    plugin_json = [n for n in names if n.endswith("/plugin.json") and n.count("/") == 1]
    assert len(plugin_json) == 1, f"expected exactly one plugin.json at depth 1, got: {plugin_json}"
    assert f"{top}/dist/index.js" in names, "dist/index.js missing (loader cannot load the frontend)"
    print("=== zip contents ===")
    for info in z.infolist():
        print(f"{info.filename:35} {info.file_size:>8}")
print("=== verification: 7 entries, plugin.json at depth 1, dist/index.js present ===")
PY
