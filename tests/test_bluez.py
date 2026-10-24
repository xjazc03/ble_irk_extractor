"""Tests for the BlueZ IRK scanner."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "ble_irk_extractor"
    / "bluez.py"
)
_SPEC = importlib.util.spec_from_file_location("ble_irk_bluez", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_BLUEZ = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BLUEZ
_SPEC.loader.exec_module(_BLUEZ)
scan_paths = _BLUEZ.scan_paths
IrkRecord = _BLUEZ.IrkRecord

IRK = "0123456789abcdef0123456789abcdef"


def sample_info(
    name: str = "Test Phone",
    address: str = "AA:BB:CC:DD:EE:FF",
    irk: str = IRK,
) -> str:
    """Return a BlueZ-style info file body."""
    return (
        "[General]\n"
        f"Name={name}\n"
        f"Address={address}\n"
        "\n"
        "[IdentityResolvingKey]\n"
        f"Key={irk}\n"
    )


class BluezScannerTests(unittest.TestCase):
    """Exercise the path scanning and parsing behaviour."""

    def test_scans_nested_info_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device_dir = Path(tmp) / "00:11:22:33:44:55" / "AA:BB:CC:DD:EE:FF"
            device_dir.mkdir(parents=True)
            (device_dir / "info").write_text(sample_info(), encoding="utf-8")

            result = scan_paths([tmp])

            self.assertEqual(len(result.records), 1)
            self.assertEqual(result.records[0].name, "Test Phone")
            self.assertEqual(result.records[0].address, "AA:BB:CC:DD:EE:FF")
            self.assertEqual(result.records[0].irk, IRK.upper())
            self.assertEqual(result.scanned_paths, (tmp,))
            self.assertEqual(result.errors, ())

    def test_scans_flattened_info_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "info").write_text(sample_info(), encoding="utf-8")

            result = scan_paths([tmp])

            self.assertEqual(len(result.records), 1)
            self.assertEqual(result.records[0].name, "Test Phone")

    def test_scans_dot_info_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "phone.info").write_text(
                sample_info(), encoding="utf-8"
            )

            result = scan_paths([tmp])

            self.assertEqual(len(result.records), 1)

    def test_direct_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bond"
            path.write_text(sample_info(), encoding="utf-8")

            result = scan_paths([str(path)])

            self.assertEqual(len(result.records), 1)
            self.assertEqual(result.scanned_paths, (str(path),))

    def test_derives_address_from_bluez_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device_dir = Path(tmp) / "00:11:22:33:44:55" / "AA:BB:CC:DD:EE:FF"
            device_dir.mkdir(parents=True)
            (device_dir / "info").write_text(
                sample_info(name="", address=""), encoding="utf-8"
            )

            result = scan_paths([tmp])

            self.assertEqual(len(result.records), 1)
            self.assertEqual(result.records[0].address, "AA:BB:CC:DD:EE:FF")
            self.assertEqual(result.records[0].name, "AA:BB:CC:DD:EE:FF")

    def test_duplicate_irk_is_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one").mkdir()
            (root / "two").mkdir()
            (root / "one" / "info").write_text(
                sample_info(name="First Name", address=""), encoding="utf-8"
            )
            (root / "two" / "info").write_text(
                sample_info(name="Second Name"), encoding="utf-8"
            )

            result = scan_paths([tmp])

            self.assertEqual(len(result.records), 1)
            self.assertEqual(result.records[0].name, "First Name")
            self.assertEqual(result.records[0].address, "AA:BB:CC:DD:EE:FF")

    def test_invalid_irk_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "info").write_text(
                sample_info(irk="not-a-valid-irk"), encoding="utf-8"
            )

            result = scan_paths([tmp])

            self.assertEqual(result.records, ())
            self.assertEqual(result.errors, ())

    def test_zero_irk_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "info").write_text(
                sample_info(irk="0" * 32), encoding="utf-8"
            )

            result = scan_paths([tmp])

            self.assertEqual(result.records, ())

    def test_missing_path_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "does-not-exist")

            result = scan_paths([missing])

            self.assertEqual(result.records, ())
            self.assertEqual(result.scanned_paths, ())
            self.assertEqual(result.missing_paths, (missing,))
            self.assertEqual(result.errors, ())

    def test_empty_directory_is_reported_as_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = scan_paths([tmp])

            self.assertEqual(result.records, ())
            self.assertEqual(result.scanned_paths, (tmp,))
            self.assertEqual(result.errors, ())

    def test_multiple_paths_are_combined(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            (Path(first) / "info").write_text(
                sample_info(
                    name="One",
                    address="AA:AA:AA:AA:AA:AA",
                    irk="1" * 32,
                ),
                encoding="utf-8",
            )
            (Path(second) / "info").write_text(
                sample_info(
                    name="Two",
                    address="BB:BB:BB:BB:BB:BB",
                    irk="2" * 32,
                ),
                encoding="utf-8",
            )

            result = scan_paths([first, second])

            self.assertEqual(len(result.records), 2)
            self.assertEqual(
                [record.name for record in result.records], ["One", "Two"]
            )


    def test_format_irk_label(self) -> None:
        from custom_components.ble_irk_extractor.bluez import format_irk_label

        rec1 = IrkRecord(
            name="Pixel 8 Pro",
            address="AA:BB:CC:DD:EE:01",
            irk="1" * 32,
            source="/config/bluez_irks",
            path="/config/bluez_irks/info1",
        )
        self.assertEqual(format_irk_label(rec1), "Pixel 8 Pro (AA:BB:CC:DD:EE:01)")

        rec2 = IrkRecord(
            name=None,
            address="AA:BB:CC:DD:EE:02",
            irk="2" * 32,
            source="/config/bluez_irks",
            path="/config/bluez_irks/info2",
        )
        self.assertEqual(format_irk_label(rec2), "AA:BB:CC:DD:EE:02")

        rec3 = IrkRecord(
            name="aa:bb:cc:dd:ee:03",
            address="AA:BB:CC:DD:EE:03",
            irk="3" * 32,
            source="/config/bluez_irks",
            path="/config/bluez_irks/info3",
        )
        self.assertEqual(format_irk_label(rec3), "AA:BB:CC:DD:EE:03")

    def test_mtime_parsing(self) -> None:
        import os
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "info"
            file_path.write_text(
                sample_info(name="Timed Device", address="AA:BB:CC:DD:EE:99", irk="9" * 32),
                encoding="utf-8",
            )
            os.utime(file_path, (1234567.0, 1234567.0))
            result = scan_paths([temp_dir])
            self.assertEqual(len(result.records), 1)
            self.assertEqual(result.records[0].mtime, 1234567.0)

if __name__ == "__main__":
    unittest.main()
