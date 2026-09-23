import unittest

from gpus import build_command


class BuildCommandTest(unittest.TestCase):
    def test_unique_name_uses_name_only(self):
        self.assertEqual(
            build_command("9070 XT", index=0, name_count=1),
            'VKD3D_FILTER_DEVICE_NAME="9070 XT" '
            'DXVK_FILTER_DEVICE_NAME="9070 XT" %command%',
        )

    def test_duplicate_name_adds_device_index(self):
        self.assertEqual(
            build_command("Radeon RX 7900 XTX", index=1, name_count=2),
            'VKD3D_FILTER_DEVICE_NAME="Radeon RX 7900 XTX" '
            'DXVK_FILTER_DEVICE_NAME="Radeon RX 7900 XTX" '
            'VKD3D_VULKAN_DEVICE=1 %command%',
        )

    def test_legacy_call_without_index(self):
        self.assertEqual(
            build_command("9070 XT"),
            'VKD3D_FILTER_DEVICE_NAME="9070 XT" '
            'DXVK_FILTER_DEVICE_NAME="9070 XT" %command%',
        )

    def test_escapes_quotes(self):
        self.assertEqual(
            build_command('A"B', index=0, name_count=2),
            'VKD3D_FILTER_DEVICE_NAME="A\\"B" '
            'DXVK_FILTER_DEVICE_NAME="A\\"B" '
            'VKD3D_VULKAN_DEVICE=0 %command%',
        )


if __name__ == "__main__":
    unittest.main()
