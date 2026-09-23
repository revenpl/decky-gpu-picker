import unittest

from gpus import build_command, list_gpus, parse_lspci


LSPCI_FIXTURE = """\
00:00.0 Host bridge [0600]: Advanced Micro Devices, Inc. [AMD] Device 15:3e [1022:153e]
01:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Navi 31 [Radeon RX 7900 XTX] [1002:744c] (rev c5)
03:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Navi 48 [Radeon RX 9070 XT] [1002:7550] (rev c0)
09:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Graphics Processing Device [Radeon Graphics] [1002:13c0] (rev c5)
09:00.1 Audio device [0403]: Advanced Micro Devices, Inc. [AMD/ATI] Navi 48 Audio [1002:ab28]
"""


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


class FakeRun:
    """Returns the configured responses; a missing key = FileNotFoundError."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        key = " ".join(cmd)
        if key not in self.responses:
            raise FileNotFoundError(key)
        r = self.responses[key]
        if isinstance(r, Exception):
            raise r
        return type("P", (), {"stdout": r, "returncode": 0})()


class ParseLspciTest(unittest.TestCase):
    def test_vga_lines_only_with_pci(self):
        got = parse_lspci(LSPCI_FIXTURE)
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0], {"name": "Radeon RX 7900 XTX", "pci": "1002:744c"})
        self.assertEqual(got[1], {"name": "Radeon RX 9070 XT", "pci": "1002:7550"})
        self.assertEqual(got[2], {"name": "Radeon Graphics", "pci": "1002:13c0"})

    def test_missing_name_bracket(self):
        got = parse_lspci(
            "05:00.0 VGA compatible controller [0300]: Unknown Vendor Unknown Device [1234:5678]"
        )
        self.assertEqual(got, [{"name": None, "pci": "1234:5678"}])


class ListGpusTest(unittest.TestCase):
    def test_prefers_vulkan(self):
        vulkan = lambda: [
            {"name": "AMD Radeon RX 9070 XT", "pci": None},
            {"name": "AMD Radeon RX 7900 XTX", "pci": None},
        ]
        run = FakeRun({})
        got = list_gpus(vulkan_devices=vulkan, lspci_run=run)
        self.assertEqual([d["source"] for d in got], ["vulkan"] * 2)
        self.assertEqual([d["index"] for d in got], [0, 1])
        self.assertEqual(got[0]["name"], "AMD Radeon RX 9070 XT")
        self.assertEqual(run.calls, [])  # lspci should not have been called

    def test_falls_back_to_lspci_when_vulkan_empty(self):
        vulkan = lambda: []
        run = FakeRun({"lspci -nn": LSPCI_FIXTURE})
        which = lambda name: "/usr/bin/lspci" if name == "lspci" else None
        got = list_gpus(vulkan_devices=vulkan, lspci_run=run, which=which)
        self.assertEqual([d["source"] for d in got], ["lspci"] * 3)
        self.assertEqual(got[1], {"name": "Radeon RX 9070 XT", "pci": "1002:7550", "source": "lspci", "index": 1})

    def test_falls_back_to_sys(self):
        vulkan = lambda: []
        run = FakeRun({})
        which = lambda name: None
        got = list_gpus(
            vulkan_devices=vulkan, lspci_run=run, which=which,
            sys_gpus=[{"name": None, "pci": "1002:7550"}],
        )
        self.assertEqual(got, [{"name": "1002:7550", "pci": "1002:7550", "source": "sys", "index": 0}])


if __name__ == "__main__":
    unittest.main()
