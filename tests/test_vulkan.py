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


class SubprocessFallbackTest(unittest.TestCase):
    """The Legion Go bug: in-process enumeration fails (PyInstaller bundle
    pollutes LD_LIBRARY_PATH -> rc=-3), but a clean-env child succeeds.
    enumerate_vulkan_devices() must fall back to the child."""

    def _patch_subprocess(self, stub):
        real = vulkan._subprocess_enumerate
        vulkan._subprocess_enumerate = stub
        return real

    def test_inprocess_failure_falls_back_to_subprocess(self):
        real = self._patch_subprocess(lambda: (
            [{"name": "AMD Ryzen Z1 Extreme (RADV PHOENIX)", "pci": "1002:15bf"}],
            "loaded /usr/lib64/libvulkan.so.1",
        ))
        try:
            got = vulkan.enumerate_vulkan_devices()
            self.assertEqual(
                got, [{"name": "AMD Ryzen Z1 Extreme (RADV PHOENIX)", "pci": "1002:15bf"}])
        finally:
            vulkan._subprocess_enumerate = real

    def test_subprocess_failure_returns_empty_with_combined_error(self):
        real = self._patch_subprocess(lambda: ([], "subprocess raised FileNotFoundError"))
        try:
            got = vulkan.enumerate_vulkan_devices()
            self.assertEqual(got, [])
            self.assertIn("subprocess", vulkan.last_error)
        finally:
            vulkan._subprocess_enumerate = real

    def test_child_process_does_not_recurse(self):
        # Inside a clean child (marker set) it must enumerate in-process only,
        # never spawn another child.
        marker = vulkan._SUBPROC_MARKER
        old = vulkan.os.environ.get(marker)
        spawned = []
        try:
            vulkan.os.environ[marker] = "1"
            vulkan._subprocess_enumerate = lambda: spawned.append(1) or ([], "no")
            got = vulkan.enumerate_vulkan_devices()
            self.assertEqual(got, [])  # in-process [] on a machine w/o driver
            self.assertEqual(spawned, [])  # the child must not have been called
        finally:
            if old is None:
                vulkan.os.environ.pop(marker, None)
            else:
                vulkan.os.environ[marker] = old


if __name__ == "__main__":
    unittest.main()
