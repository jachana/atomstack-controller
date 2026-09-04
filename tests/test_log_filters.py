import unittest

from atomstack.ui import filter_log, log_level


ENTRIES = [
    ("TX", "b'$I\\n' [hex: 24 49 0a]"),
    ("RX", "[VER:V1.055.Oct 13 2023:]"),
    ("RX", "<Idle|MPos:0,0,0|FS:0,0>"),
    ("RX", "error:1"),
    ("FAULT", "Controller reported error:1"),
    ("REPORT", "Could not write diagnostic report"),
]


class LogFilterTests(unittest.TestCase):
    def test_default_hides_normal_usb_traffic(self):
        visible = filter_log(ENTRIES, "Warnings and errors")
        self.assertEqual([level for kind, text in visible for level in [log_level(kind, text)]],
                         ["error", "error", "warning"])
        self.assertFalse(any("MPos" in text or "$I" in text or "VER:" in text for kind, text in visible))

    def test_errors_only(self):
        visible = filter_log(ENTRIES, "Errors only")
        self.assertEqual(len(visible), 2)
        self.assertTrue(all(log_level(*entry) == "error" for entry in visible))

    def test_information_includes_identity_without_raw_status(self):
        visible = filter_log(ENTRIES, "Information")
        self.assertTrue(any("VER:" in text for kind, text in visible))
        self.assertFalse(any("MPos" in text for kind, text in visible))

    def test_usb_traffic_shows_everything(self):
        self.assertEqual(filter_log(ENTRIES, "USB traffic"), tuple(ENTRIES))


if __name__ == "__main__":
    unittest.main()
