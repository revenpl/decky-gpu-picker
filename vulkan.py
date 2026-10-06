"""Direct enumeration of Vulkan devices via libvulkan.so.1 (ctypes, stdlib).

Zero third-party runtime dependencies: we use the Vulkan loader, which is
mandatory on SteamOS (no title could launch without it).

The returned "name" is exactly VkPhysicalDeviceProperties::deviceName - the same
string VKD3D_FILTER_DEVICE_NAME is matched against (vkd3d enumerates the same way).

On any failure (missing loader, missing driver, FFI error) we return [] so the
caller can fall back (lspci -> /sys).

Layout note: VkPhysicalDeviceProperties starts with apiVersion (per
vulkan_core.h; verified empirically against a real driver). The Python
mirror is padded far beyond the real struct size so the loader's by-value
write can never overflow it.

Environment note (root cause found on the Legion Go, 2026-10): the Decky
Loader is a PyInstaller bundle that ships its own libstdc++/libz/libffi and
puts that dir in LD_LIBRARY_PATH. When the Vulkan loader dlopens the radeon
ICD inside the backend, the ICD's dependencies resolve to the bundle copies ->
VK_ERROR_INCOMPATIBLE_DRIVER -> zero devices, even though the exact same code
works in a normal shell. The linker's search list is fixed at process start
and cannot be repaired in-process, so enumeration falls back to a clean-env
child process (see _clean_subprocess_env / _subprocess_enumerate).
"""

import ctypes
import ctypes.util
import json
import os
import shutil
import subprocess
import sys

__all__ = ["load_vulkan", "enumerate_vulkan_devices", "last_error"]

# Why this module is import-safe and dependency-free, and where failures go:
# the backend (a Nuitka-frozen process, run as root by systemd) has a different
# environment from a normal shell, so the loader/driver may resolve differently
# there. Any failure is recorded in `last_error` for diagnostics instead of
# being silently swallowed, so the caller can report the real cause.
last_error = ""

# Explicit loader paths, tried after the standard lookup, to survive a backend
# environment whose LD_LIBRARY_PATH / rpath resolves libvulkan.so.1 to a
# different (possibly wrong) library than a normal shell does.
_LIB_PATHS = (
    "/usr/lib/x86_64-linux-gnu/libvulkan.so.1",
    "/usr/lib64/libvulkan.so.1",
    "/usr/lib/libvulkan.so.1",
)

# Set in a child process spawned by _subprocess_enumerate so the child never
# spawns yet another one (recursion guard).
_SUBPROC_MARKER = "DECKY_GPU_PICKER_SUBPROC"

# --- Vulkan result codes ---
VK_SUCCESS = 0
VK_INCOMPLETE = -13
VK_ERROR_INCOMPATIBLE_DRIVER = -9  # (informational) no ICD driver

# --- sType ---
VK_STRUCTURE_TYPE_APPLICATION_INFO = 0
VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO = 1

_VK_API_VERSION_1_0 = 1 << 22  # VK_MAKE_VERSION(1, 0, 0)


class VkApplicationInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_int32),
        ("pNext", ctypes.c_void_p),
        ("pApplicationName", ctypes.c_char_p),
        ("applicationVersion", ctypes.c_uint32),
        ("pEngineName", ctypes.c_char_p),
        ("engineVersion", ctypes.c_uint32),
        ("apiVersion", ctypes.c_uint32),
    ]


class VkInstanceCreateInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_int32),
        ("pNext", ctypes.c_void_p),
        ("flags", ctypes.c_uint32),
        ("pApplicationInfo", ctypes.c_void_p),
        ("enabledLayerCount", ctypes.c_uint32),
        ("ppEnabledLayerNames", ctypes.POINTER(ctypes.c_char_p)),
        ("enabledExtensionCount", ctypes.c_uint32),
        ("ppEnabledExtensionNames", ctypes.POINTER(ctypes.c_char_p)),
    ]


class VkPhysicalDeviceProperties(ctypes.Structure):
    # Vulkan ABI: apiVersion, driverVersion, vendorID, deviceID, deviceType,
    # deviceName[256], pipelineCacheUUID[16], limits, sparseProperties.
    # The 8 KiB tail absorbs limits+sparseProperties with large headroom.
    _fields_ = [
        ("apiVersion", ctypes.c_uint32),
        ("driverVersion", ctypes.c_uint32),
        ("vendorID", ctypes.c_uint32),
        ("deviceID", ctypes.c_uint32),
        ("deviceType", ctypes.c_uint32),
        ("deviceName", ctypes.c_char * 256),
        ("_tail", ctypes.c_ubyte * 8192),
    ]


def load_vulkan():
    """Loads libvulkan.so.1, preferring the absolute system path.

    The standard name lookup ("libvulkan.so.1") is resolved by the dynamic
    linker using the *process* LD_LIBRARY_PATH. In a Nuitka-frozen Decky
    backend that path typically points at the plugin's own bundle dir, so the
    name can resolve to the wrong (or a missing) library there, even though it
    resolves correctly in a normal shell. Loading the absolute system path
    first bypasses LD_LIBRARY_PATH entirely and is the same library a shell
    would get, so it is safe in both environments.

    Records which path succeeded (and every failure) in `last_error`.
    """
    global last_error
    errors = []
    # 1) Absolute system paths first: immune to a polluted LD_LIBRARY_PATH.
    for path in _LIB_PATHS:
        try:
            lib = ctypes.CDLL(path)
            last_error = f"loaded {path} (explicit system path)"
            return lib
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    # 2) Then the standard lookup (covers non-standard distro layouts).
    for name in ("libvulkan.so.1", "libvulkan.so"):
        try:
            lib = ctypes.CDLL(name)
            last_error = f"loaded {name}"
            return lib
        except OSError as exc:
            errors.append(f"{name}: {exc}")
    # 3) Finally, whatever the loader can find by soname.
    path = ctypes.util.find_library("vulkan")
    if path:
        try:
            lib = ctypes.CDLL(path)
            last_error = f"loaded {path} (find_library)"
            return lib
        except OSError as exc:
            errors.append(f"find_library->{path}: {exc}")
    last_error = "no libvulkan found: " + " | ".join(errors)
    return None


def _fail(reason):
    global last_error
    last_error = reason


def _set_argtypes(lib):
    """Pin the calling convention for every entry point we call.

    Without explicit argtypes, a GPU handle obtained by indexing a
    ``c_void_p`` array (``devs[i]``) is a plain Python int that ctypes
    would pass as a 32-bit ``c_int`` - truncating the 64-bit pointer and
    segfaulting inside vkGetPhysicalDeviceProperties. Declaring the
    argument as ``c_void_p`` passes the full 64-bit handle. (Verified on
    the Legion Go / AMD Phoenix1: 0/8 without argtypes, 10/10 with.)
    """
    lib.vkCreateInstance.argtypes = [
        ctypes.POINTER(VkInstanceCreateInfo),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.vkCreateInstance.restype = ctypes.c_int32

    lib.vkEnumeratePhysicalDevices.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.vkEnumeratePhysicalDevices.restype = ctypes.c_int32

    lib.vkGetPhysicalDeviceProperties.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(VkPhysicalDeviceProperties),
    ]
    lib.vkGetPhysicalDeviceProperties.restype = None

    lib.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.vkDestroyInstance.restype = ctypes.c_int32


def _subprocess_enumerate():
    """Fallback: enumerate in a fresh child process with a clean environment.

    Root cause (verified on the Legion Go): the Decky Loader is a PyInstaller
    bundle whose directory (e.g. /tmp/_MEI...) sits in LD_LIBRARY_PATH and
    contains its own libstdc++.so.6 / libz.so.1 / libffi.so.8. When the Vulkan
    loader dlopens the ICD (libvulkan_radeon.so) inside the backend, the ICD's
    dependencies resolve to the bundle copies -> VK_ERROR_INCOMPATIBLE_DRIVER
    -> zero devices. The dynamic linker's search list is fixed at process
    start, so editing os.environ in-process cannot repair it (verified: the
    failure persists after popping LD_* in-process). A fresh child process
    started WITHOUT any LD_* variables resolves the system libraries and
    enumerates correctly (verified on the device).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    interp = shutil.which("python3") or "/usr/bin/python3"
    code = (
        "import sys, json;"
        f"sys.path.insert(0, {here!r});"
        "import vulkan;"
        "print(json.dumps({'d': vulkan.enumerate_vulkan_devices(),"
        " 'e': vulkan.last_error}))"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("LD_")}
    env[_SUBPROC_MARKER] = "1"
    try:
        r = subprocess.run(
            [interp, "-c", code],
            env=env, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"subprocess raised {exc!r}"
    if r.returncode != 0:
        return [], f"subprocess rc={r.returncode} stderr={r.stderr.strip()[:200]}"
    lines = r.stdout.strip().splitlines()
    try:
        payload = json.loads(lines[-1]) if lines else {}
    except ValueError:
        return [], f"subprocess output unparseable: {r.stdout.strip()[:200]!r}"
    return (payload.get("d") or []), (payload.get("e") or "subprocess returned nothing")


def _enumerate_inprocess():
    """The direct ctypes enumeration (see module docstring)."""
    lib = load_vulkan()
    if lib is None:
        return []
    _set_argtypes(lib)
    inst = ctypes.c_void_p()
    try:
        ai = VkApplicationInfo()
        ai.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO
        ai.pApplicationName = b"decky-gpu-picker"
        ai.apiVersion = _VK_API_VERSION_1_0

        ci = VkInstanceCreateInfo()
        ci.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO
        ci.pApplicationInfo = ctypes.addressof(ai)

        rc = lib.vkCreateInstance(ctypes.byref(ci), None, ctypes.byref(inst))
        if rc != VK_SUCCESS:
            _fail(f"vkCreateInstance rc={rc} (e.g. {VK_ERROR_INCOMPATIBLE_DRIVER} = no ICD)")
            return []

        n = ctypes.c_uint32(0)
        rc = lib.vkEnumeratePhysicalDevices(inst, ctypes.byref(n), None)
        if rc != VK_SUCCESS or n.value == 0:
            _fail(f"vkEnumeratePhysicalDevices(count) rc={rc} n={n.value}")
            return []

        devs = (ctypes.c_void_p * n.value)()
        rc = lib.vkEnumeratePhysicalDevices(inst, ctypes.byref(n), devs)
        if rc not in (VK_SUCCESS, VK_INCOMPLETE):
            _fail(f"vkEnumeratePhysicalDevices(devs) rc={rc}")
            return []

        out = []
        for i in range(n.value):
            props = VkPhysicalDeviceProperties()
            lib.vkGetPhysicalDeviceProperties(devs[i], ctypes.byref(props))
            name = props.deviceName.decode("utf-8", "replace").strip()
            if name:
                # PCI id from vendorID/deviceID - same format as `lspci -nn`,
                # useful to tell two identical cards apart in the UI.
                out.append({
                    "name": name,
                    "pci": f"{props.vendorID:04x}:{props.deviceID:04x}",
                })
        if not out:
            _fail(f"enumerated {n.value} device(s) but none had a readable deviceName")
        return out
    except Exception as exc:
        _fail(f"exception: {exc!r}")
        return []
    finally:
        if inst.value is not None:
            try:
                lib.vkDestroyInstance(inst, None)
            except Exception:
                pass


def enumerate_vulkan_devices():
    """Returns [{"name": str, "pci": "<vendor:device>"}] for each Vulkan device.

    [] on any failure (missing loader/driver, FFI error). The specific reason
    is always recorded in `last_error` for diagnostics.

    Tries the direct ctypes enumeration first; if that yields no devices,
    falls back to a clean-env child process (see _subprocess_enumerate). The
    child sets _SUBPROC_MARKER so it never re-spawns (recursion guard) and,
    because it starts without any LD_* variables, resolves the *system*
    Vulkan driver libraries - the exact fix for the PyInstaller-bundle
    LD_LIBRARY_PATH pollution described in the module docstring.
    """
    global last_error

    if os.environ.get(_SUBPROC_MARKER) == "1":
        # We ARE the clean child: in-process only, never spawn again.
        return _enumerate_inprocess()

    devices = _enumerate_inprocess()
    if devices:
        return devices

    # In-process failed (typically rc=-3 from the bundle's LD_LIBRARY_PATH).
    # Retry in a fresh process with the dynamic-linker search list clean.
    child, child_err = _subprocess_enumerate()
    if child:
        last_error = f"in-process: {last_error} | subprocess: {child_err}"
        return child

    # Both failed. Keep the most useful reason for diagnostics: prefer the
    # in-process one (it is the real FFI error), note the subprocess attempt.
    if last_error:
        last_error = f"in-process: {last_error} | subprocess: {child_err}"
    else:
        last_error = f"subprocess: {child_err}"
    return []
