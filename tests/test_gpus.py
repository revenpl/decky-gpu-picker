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
    def test_unique_name_uses_token_only(self):
        # "9070 XT" -> model token "9070" (a substring of the Vulkan device name).
        self.assertEqual(
            build_command("9070 XT", index=0, name_count=1),
            'VKD3D_FILTER_DEVICE_NAME="9070" '
            'DXVK_FILTER_DEVICE_NAME="9070" %command%',
        )

    def test_duplicate_name_adds_device_index(self):
        # "Radeon RX 7900 XTX" -> token "7900"; duplicated name -> also device index.
        self.assertEqual(
            build_command("Radeon RX 7900 XTX", index=1, name_count=2),
            'VKD3D_FILTER_DEVICE_NAME="7900" '
            'DXVK_FILTER_DEVICE_NAME="7900" '
            'VKD3D_VULKAN_DEVICE=1 %command%',
        )

    def test_legacy_call_without_index(self):
        self.assertEqual(
            build_command("9070 XT"),
            'VKD3D_FILTER_DEVICE_NAME="9070" '
            'DXVK_FILTER_DEVICE_NAME="9070" %command%',
        )

    def test_escapes_quotes(self):
        # No 3-4 digit sequence -> fall back to the raw name, still escaped.
        self.assertEqual(
            build_command('A"B', index=0, name_count=2),
            'VKD3D_FILTER_DEVICE_NAME="A\\"B" '
            'DXVK_FILTER_DEVICE_NAME="A\\"B" '
            'VKD3D_VULKAN_DEVICE=0 %command%',
        )


class FamilyStringRegressionTest(unittest.TestCase):
    """The real bug: the lspci fallback reports a *family* string
    (e.g. 'Radeon RX 9070/9070 XT/9070 GRE') that is NOT a substring of any
    concrete Vulkan device name, so a command built from it never matched.
    The fix must reduce such names to a model token that IS a substring of the
    real Vulkan name."""

    REAL_VULKAN = {
        "9070": "AMD Radeon RX 9070 XT (RADV GFX1201)",
        "7900": "AMD Radeon RX 7900 XTX (RADV NAVI31)",
    }

    def test_9070_family_string_reduces_to_matching_token(self):
        from gpus import _extract_model_token
        token = _extract_model_token("Radeon RX 9070/9070 XT/9070 GRE")
        self.assertEqual(token, "9070")
        # The token must be a (case-insensitive) substring of the real Vulkan name.
        self.assertIn(token.lower(), self.REAL_VULKAN["9070"].lower())
        # And the OLD full family string must NOT be (this is the bug).
        self.assertNotIn(
            "Radeon RX 9070/9070 XT/9070 GRE", self.REAL_VULKAN["9070"],
        )

    def test_7900_family_string_reduces_to_matching_token(self):
        from gpus import _extract_model_token
        token = _extract_model_token("Radeon RX 7900 XT/7900 XTX/7900 GRE/7900M")
        self.assertEqual(token, "7900")
        self.assertIn(token.lower(), self.REAL_VULKAN["7900"].lower())

    def test_full_vulkan_name_also_reduces_to_matching_token(self):
        # When the name came from the working Vulkan path, the token must still
        # be a substring of the (identical) Vulkan device name.
        from gpus import _extract_model_token
        for vulk in self.REAL_VULKAN.values():
            token = _extract_model_token(vulk)
            self.assertIn(token.lower(), vulk.lower())


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
