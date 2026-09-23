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

__all__ = ["load_vulkan", "enumerate_vulkan_devices"]

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
    """Loads libvulkan.so.1 via the standard lookup path. None if missing."""
    for name in ("libvulkan.so.1", "libvulkan.so"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    path = ctypes.util.find_library("vulkan")
    if path:
        try:
            return ctypes.CDLL(path)
        except OSError:
            pass
    return None


def enumerate_vulkan_devices():
    """Returns [{"name": str, "pci": "<vendor:device>"}] for each Vulkan device.

    [] on any failure (missing loader/driver, FFI error).
    """
    lib = load_vulkan()
    if lib is None:
        return []
    inst = ctypes.c_void_p()
    try:
        ai = VkApplicationInfo()
        ai.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO
        ai.pApplicationName = b"decky-gpu-picker"
        ai.apiVersion = _VK_API_VERSION_1_0

        ci = VkInstanceCreateInfo()
        ci.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO
        ci.pApplicationInfo = ctypes.addressof(ai)

        if lib.vkCreateInstance(ctypes.byref(ci), None, ctypes.byref(inst)) != VK_SUCCESS:
            return []  # e.g. VK_ERROR_INCOMPATIBLE_DRIVER - no driver

        n = ctypes.c_uint32(0)
        if (
            lib.vkEnumeratePhysicalDevices(inst, ctypes.byref(n), None) != VK_SUCCESS
            or n.value == 0
        ):
            return []

        devs = (ctypes.c_void_p * n.value)()
        if (
            lib.vkEnumeratePhysicalDevices(inst, ctypes.byref(n), devs)
            not in (VK_SUCCESS, VK_INCOMPLETE)
        ):
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
        return out
    except Exception:
        return []
    finally:
        if inst.value is not None:
            try:
                lib.vkDestroyInstance(inst, None)
            except Exception:
                pass
