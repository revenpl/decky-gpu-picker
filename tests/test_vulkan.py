import unittest

import vulkan


class VulkanContractTest(unittest.TestCase):
    def test_exposes_enumerate(self):
        self.assertTrue(callable(vulkan.enumerate_vulkan_devices))

    def test_returns_list_of_dicts(self):
        # The build container has no driver -> expect a graceful [] (on a GPU: real list).
        devices = vulkan.enumerate_vulkan_devices()
        self.assertIsInstance(devices, list)
        for d in devices:
            self.assertIn("name", d)
            self.assertIsInstance(d["name"], str)
            self.assertIn("pci", d)

    def test_loads_libvulkan_when_present(self):
        lib = vulkan.load_vulkan()
        if lib is not None:
            # These symbols must exist - otherwise the FFI would not work.
            self.assertTrue(hasattr(lib, "vkEnumeratePhysicalDevices"))
            self.assertTrue(hasattr(lib, "vkGetPhysicalDeviceProperties"))


if __name__ == "__main__":
    unittest.main()
