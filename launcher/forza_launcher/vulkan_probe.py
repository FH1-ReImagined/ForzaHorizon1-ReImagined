"""The Vulkan devices of this PC and the API version their driver reports (#672).

Runs in a process of its own (gpu.vulkan_devices starts it: `forza-launcher --vulkan-devices FILE`, or this
file with Python), so a broken graphics driver can't take the launcher down with it. It creates a Vulkan 1.0
instance through the system's loader (vulkan-1.dll, libvulkan.so.1), asks each physical device for its
properties and writes them to FILE as JSON: {"devices": [{"name", "vendor_id", "device_id", "type", "api",
"driver"}]}. Standard library only: it also runs as a plain script.
"""
import ctypes
import json
import sys

VK_API_1_0 = 1 << 22
PROPERTIES_SIZE = 4096   # VkPhysicalDeviceProperties is 824 bytes; only its first fields are read


class _AppInfo(ctypes.Structure):
    _fields_ = [("sType", ctypes.c_uint32), ("pNext", ctypes.c_void_p), ("pApplicationName", ctypes.c_char_p),
                ("applicationVersion", ctypes.c_uint32), ("pEngineName", ctypes.c_char_p),
                ("engineVersion", ctypes.c_uint32), ("apiVersion", ctypes.c_uint32)]


class _InstanceInfo(ctypes.Structure):
    _fields_ = [("sType", ctypes.c_uint32), ("pNext", ctypes.c_void_p), ("flags", ctypes.c_uint32),
                ("pApplicationInfo", ctypes.POINTER(_AppInfo)), ("enabledLayerCount", ctypes.c_uint32),
                ("ppEnabledLayerNames", ctypes.c_void_p), ("enabledExtensionCount", ctypes.c_uint32),
                ("ppEnabledExtensionNames", ctypes.c_void_p)]


def api_text(version: int) -> str:
    """VK_API_VERSION_MAJOR.MINOR.PATCH of a packed version."""
    return f"{(version >> 22) & 0x7F}.{(version >> 12) & 0x3FF}.{version & 0xFFF}"


def _loader():
    if sys.platform == "win32":
        return ctypes.WinDLL("vulkan-1.dll")
    if sys.platform == "darwin":
        raise OSError("no Vulkan loader on macOS")
    try:
        return ctypes.CDLL("libvulkan.so.1")
    except OSError:
        return ctypes.CDLL("libvulkan.so")


def devices() -> list:
    vk = _loader()
    vk.vkCreateInstance.restype = ctypes.c_int32
    vk.vkCreateInstance.argtypes = [ctypes.POINTER(_InstanceInfo), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    vk.vkEnumeratePhysicalDevices.restype = ctypes.c_int32
    vk.vkEnumeratePhysicalDevices.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    vk.vkGetPhysicalDeviceProperties.restype = None
    vk.vkGetPhysicalDeviceProperties.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    vk.vkDestroyInstance.restype = None
    vk.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    app = _AppInfo(0, None, b"forza-launcher", 1, b"forza-launcher", 1, VK_API_1_0)
    info = _InstanceInfo(1, None, 0, ctypes.pointer(app), 0, None, 0, None)
    instance = ctypes.c_void_p()
    result = vk.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(instance))
    if result != 0:
        raise OSError(f"vkCreateInstance returned {result}")
    try:
        count = ctypes.c_uint32(0)
        if vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), None) < 0:
            raise OSError("vkEnumeratePhysicalDevices failed")
        handles = (ctypes.c_void_p * max(count.value, 1))()
        if vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), handles) < 0:
            raise OSError("vkEnumeratePhysicalDevices failed")
        out = []
        for i in range(count.value):
            props = ctypes.create_string_buffer(PROPERTIES_SIZE)
            vk.vkGetPhysicalDeviceProperties(handles[i], props)
            api, driver, vendor, device, kind = (int.from_bytes(props.raw[o:o + 4], "little") for o in range(0, 20, 4))
            name = props.raw[20:276].split(b"\0", 1)[0].decode("utf-8", "replace")
            out.append({"name": name, "vendor_id": vendor, "device_id": device, "type": kind,
                        "api": api_text(api), "driver": driver})
        return out
    finally:
        vk.vkDestroyInstance(instance, None)


def main(argv) -> int:
    try:
        data = {"devices": devices()}
    except Exception as e:   # no loader, no driver, no device: the launcher reads "unknown"
        data = {"error": f"{type(e).__name__}: {e}"}
    text = json.dumps(data)
    if argv:
        with open(argv[0], "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
