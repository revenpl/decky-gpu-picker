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
"""

import ctypes
import ctypes.util

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
    """Loads libvulkan.so.1 via the standard lookup path. None if missing.

    Records the resolution result (and any error) in `last_error` for
    diagnostics. Also tries explicit system paths, in case the backend's
    environment resolves the standard name to a different library.
    """
    global last_error
    errors = []
    for name in ("libvulkan.so.1", "libvulkan.so"):
        try:
            lib = ctypes.CDLL(name)
            last_error = f"loaded {name}"
            return lib
        except OSError as exc:
            errors.append(f"{name}: {exc}")
    path = ctypes.util.find_library("vulkan")
    if path:
        try:
            lib = ctypes.CDLL(path)
            last_error = f"loaded {path}"
            return lib
        except OSError as exc:
            errors.append(f"find_library->{path}: {exc}")
    for path in _LIB_PATHS:
        try:
            lib = ctypes.CDLL(path)
            last_error = f"loaded {path}"
            return lib
        except OSError as exc:
            errors.append(f"{path}: {exc}")
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


def enumerate_vulkan_devices():
    """Returns [{"name": str, "pci": "<vendor:device>"}] for each Vulkan device.

    [] on any failure (missing loader/driver, FFI error). The specific reason
    is always recorded in `last_error` for diagnostics.
    """
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
