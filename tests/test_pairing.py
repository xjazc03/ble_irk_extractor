"""Unit tests for the BLE IRK pairing manager."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

# Setup mocks
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.mock_ha import setup_ha_mocks
setup_ha_mocks()

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant

from custom_components.ble_irk_extractor.pairing import (
    BleIrkPairingManager,
    DISCOVERY_TIMEOUT_SECONDS,
    format_adapter_label,
)
from custom_components.ble_irk_extractor.bluez import IrkRecord, ScanResult


class PairingTests(unittest.IsolatedAsyncioTestCase):
    """Test BleIrkPairingManager discovery, pairing, and extraction."""


    def test_format_adapter_label(self) -> None:
        # 1. iface and product distinct
        label1 = format_adapter_label("00:11:22:33:44:55", name="hci0", product="Bluetooth Adapter", iface="hci0")
        self.assertEqual(label1, "hci0 - Bluetooth Adapter (00:11:22:33:44:55)")

        # 2. iface only
        label2 = format_adapter_label("00:11:22:33:44:55", name="hci0", iface="hci0")
        self.assertEqual(label2, "hci0 (00:11:22:33:44:55)")

        # 3. proxy / custom name without iface
        label3 = format_adapter_label("AA:BB:CC:DD:EE:01", name="Living Room Proxy")
        self.assertEqual(label3, "Living Room Proxy (AA:BB:CC:DD:EE:01)")

        # 4. default / active scanner fallback
        label4 = format_adapter_label("default", name="Home Assistant Bluetooth (2 active scanners)")
        self.assertEqual(label4, "Home Assistant Bluetooth (2 active scanners)")

        # 5. Raw address fallback
        label5 = format_adapter_label("00:11:22:33:44:55")
        self.assertEqual(label5, "00:11:22:33:44:55")

    async def test_async_get_adapters_from_bluetooth_adapters(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        adapters = await manager.async_get_adapters()
        self.assertEqual(len(adapters), 1)
        self.assertEqual(adapters[0]["address"], "00:11:22:33:44:55")
        self.assertIn("Bluetooth Adapter", adapters[0]["name"])

    async def test_async_get_adapters_scanner_count_fallback(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        import bluetooth_adapters
        mock_mgr = MagicMock()
        mock_mgr.refresh = AsyncMock()
        mock_mgr.adapters = {}

        with patch.object(bluetooth_adapters, "get_adapters", return_value=mock_mgr), \
             patch.object(bluetooth, "async_scanner_count", return_value=2):
            adapters = await manager.async_get_adapters()
            self.assertEqual(len(adapters), 1)
            self.assertEqual(adapters[0]["address"], "default")
            self.assertIn("2 active scanners", adapters[0]["name"])

    async def test_async_get_adapters_error_fallback(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        import bluetooth_adapters
        with patch.object(bluetooth_adapters, "get_adapters", side_effect=RuntimeError("D-Bus down")), \
             patch.object(bluetooth, "async_scanner_count", return_value=0):
            adapters = await manager.async_get_adapters()
            self.assertEqual(adapters, [])

    async def test_async_get_adapters_none_detected_logs_guidance(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        import bluetooth_adapters
        mock_mgr = MagicMock()
        mock_mgr.refresh = AsyncMock()
        mock_mgr.adapters = {}

        with patch.object(bluetooth_adapters, "get_adapters", return_value=mock_mgr), \
             patch.object(bluetooth, "async_scanner_count", return_value=0), \
             self.assertLogs("custom_components.ble_irk_extractor.pairing", level="WARNING") as log:
            adapters = await manager.async_get_adapters()
            self.assertEqual(adapters, [])
            self.assertTrue(any("Settings -> Devices & Services" in m for m in log.output))

    async def test_async_discover_devices(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        mock_scanner = MagicMock()
        unsub = MagicMock()

        def fake_register(hass, callback, matcher, mode=None):
            callback(BluetoothServiceInfoBleak("AA:BB:CC:DD:EE:01", name="Device Low", rssi=-80), None)
            callback(BluetoothServiceInfoBleak("AA:BB:CC:DD:EE:02", name="Device High", rssi=-40), None)
            return unsub

        with patch.object(bluetooth, "async_get_scanner", return_value=mock_scanner), \
             patch.object(bluetooth, "async_register_callback", side_effect=fake_register), \
             patch("asyncio.sleep", new=AsyncMock()):
            devices = await manager.async_discover_devices("00:11:22:33:44:55")
            self.assertEqual(len(devices), 2)
            self.assertEqual(devices[0]["address"], "AA:BB:CC:DD:EE:02")
            self.assertEqual(devices[0]["rssi"], -40)
            self.assertEqual(devices[1]["address"], "AA:BB:CC:DD:EE:01")
            self.assertEqual(devices[1]["rssi"], -80)
            unsub.assert_called_once()

    async def test_async_discover_devices_no_scanner(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        with patch.object(bluetooth, "async_get_scanner", return_value=None), \
             patch.object(bluetooth, "async_scanner_count", return_value=0):
            devices = await manager.async_discover_devices("00:11:22:33:44:55")
            self.assertEqual(devices, [])

    async def test_async_pair_device(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        # Successful pair
        with patch.object(bluetooth, "async_pair_device", new=AsyncMock(return_value=True)):
            success = await manager.async_pair_device("AA:BB:CC:DD:EE:FF")
            self.assertTrue(success)

        # Pair failure
        with patch.object(bluetooth, "async_pair_device", new=AsyncMock(return_value=False)):
            success = await manager.async_pair_device("AA:BB:CC:DD:EE:FF")
            self.assertFalse(success)

        # Exception during pair
        with patch.object(bluetooth, "async_pair_device", new=AsyncMock(side_effect=Exception("D-Bus error"))):
            success = await manager.async_pair_device("AA:BB:CC:DD:EE:FF")
            self.assertFalse(success)

        # No async_pair_device available on core
        with patch.object(bluetooth, "async_pair_device", None, create=False):
            if hasattr(bluetooth, "async_pair_device"):
                delattr(bluetooth, "async_pair_device")
            success = await manager.async_pair_device("AA:BB:CC:DD:EE:FF")
            self.assertFalse(success)

    def test_extract_irk_for_device(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        rec1 = IrkRecord(
            name="My Phone",
            address="AA:BB:CC:DD:EE:FF",
            irk="0123456789ABCDEF0123456789ABCDEF",
            source="/config/bluez_irks",
            path="/config/bluez_irks/info",
        )
        rec2 = IrkRecord(
            name="Earbuds",
            address="11:22:33:44:55:66",
            irk="FEDCBA9876543210FEDCBA9876543210",
            source="/config/bluez_irks",
            path="/config/bluez_irks/earbuds.info",
        )
        fake_result = ScanResult(
            records=(rec1, rec2),
            scanned_paths=("/config/bluez_irks",),
            missing_paths=(),
            errors=(),
            scanned_at=None,
        )

        with patch("custom_components.ble_irk_extractor.pairing.scan_paths", return_value=fake_result):
            # Match by address (case-insensitive)
            match1 = manager.extract_irk_for_device("aa:bb:cc:dd:ee:ff", ["/config/bluez_irks"])
            self.assertIsNotNone(match1)
            self.assertEqual(match1.irk, "0123456789ABCDEF0123456789ABCDEF")

            # Match by name
            match2 = manager.extract_irk_for_device("my phone", ["/config/bluez_irks"])
            self.assertIsNotNone(match2)
            self.assertEqual(match2.name, "My Phone")

            # No match
            match3 = manager.extract_irk_for_device("99:99:99:99:99:99", ["/config/bluez_irks"])
            self.assertIsNone(match3)

    async def test_async_wait_for_bond_record(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        rec = IrkRecord(
            name="My Device",
            address="AA:BB:CC:DD:EE:FF",
            irk="0123456789ABCDEF0123456789ABCDEF",
            source="/config/bluez_irks",
            path="/config/bluez_irks/info",
        )

        # Immediate find
        with patch.object(manager, "extract_irk_for_device", return_value=rec):
            result = await manager.async_wait_for_bond_record("AA:BB:CC:DD:EE:FF", ["/config/bluez_irks"])
            self.assertEqual(result, rec)

        # Retry find
        calls = [None, rec]
        with patch.object(manager, "extract_irk_for_device", side_effect=lambda *args: calls.pop(0)), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await manager.async_wait_for_bond_record("AA:BB:CC:DD:EE:FF", ["/config/bluez_irks"])
            self.assertEqual(result, rec)

        # Timeout / not found
        with patch.object(manager, "extract_irk_for_device", return_value=None), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await manager.async_wait_for_bond_record("AA:BB:CC:DD:EE:FF", ["/config/bluez_irks"])
            self.assertIsNone(result)


    def test_format_device_label(self) -> None:
        """Test formatting device labels across different advertisement profiles."""
        from custom_components.ble_irk_extractor.pairing import (
            APPLE_MANUFACTURER_ID,
            format_device_label,
        )

        # 1. Named device with RSSI
        label1 = format_device_label("AA:BB:CC:DD:EE:01", name="Josh's iPhone", rssi=-54)
        self.assertEqual(label1, "Josh's iPhone (AA:BB:CC:DD:EE:01) [-54 dBm]")

        # 2. Named device without RSSI
        label2 = format_device_label("AA:BB:CC:DD:EE:01", name="Josh's iPhone", rssi=None)
        self.assertEqual(label2, "Josh's iPhone (AA:BB:CC:DD:EE:01)")

        # 3. Unnamed Apple device with manufacturer ID 0x004c (76) and RSSI
        label3 = format_device_label(
            "AA:BB:CC:DD:EE:02",
            name=None,
            rssi=-62,
            manufacturer_data={APPLE_MANUFACTURER_ID: b"\x10\x05"},
        )
        self.assertEqual(label3, "Apple Device (AA:BB:CC:DD:EE:02) [-62 dBm]")

        # 4. Unnamed Apple device without RSSI
        label4 = format_device_label(
            "AA:BB:CC:DD:EE:02",
            name="",
            rssi=None,
            manufacturer_data={76: b"\x10\x05"},
        )
        self.assertEqual(label4, "Apple Device (AA:BB:CC:DD:EE:02)")

        # 5. Device whose name equals address (unnamed) but has Apple manufacturer ID
        label5 = format_device_label(
            "AA:BB:CC:DD:EE:02",
            name="aa:bb:cc:dd:ee:02",
            rssi=-45,
            manufacturer_data={76: b"\x10\x05"},
        )
        self.assertEqual(label5, "Apple Device (AA:BB:CC:DD:EE:02) [-45 dBm]")

        # 6. Unnamed generic BLE peripheral with RSSI
        label6 = format_device_label("AA:BB:CC:DD:EE:03", name=None, rssi=-78)
        self.assertEqual(label6, "AA:BB:CC:DD:EE:03 [-78 dBm]")

        # 7. Unnamed generic BLE peripheral without RSSI
        label7 = format_device_label("AA:BB:CC:DD:EE:03", name="", rssi=None)
        self.assertEqual(label7, "AA:BB:CC:DD:EE:03")

    async def test_async_discover_devices_metadata_and_sorting(self) -> None:
        """Test that discovered devices include labels, manufacturer metadata, and are sorted by RSSI."""
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        mock_scanner = MagicMock()
        unsub = MagicMock()

        def fake_register(hass, callback, matcher, mode=None):
            # Unnamed generic beacon (-85 dBm)
            callback(BluetoothServiceInfoBleak("AA:BB:CC:DD:EE:03", name=None, rssi=-85), None)
            # Unnamed Apple device (-50 dBm)
            callback(
                BluetoothServiceInfoBleak(
                    "AA:BB:CC:DD:EE:02",
                    name=None,
                    rssi=-50,
                    manufacturer_data={76: b"\x12\x02"},
                ),
                None,
            )
            # Named device close by (-35 dBm)
            callback(BluetoothServiceInfoBleak("AA:BB:CC:DD:EE:01", name="Josh's iPhone", rssi=-35), None)
            # Device with no RSSI (None)
            callback(BluetoothServiceInfoBleak("AA:BB:CC:DD:EE:04", name="Distant Device", rssi=None), None)
            return unsub

        with patch.object(bluetooth, "async_get_scanner", return_value=mock_scanner), \
             patch.object(bluetooth, "async_register_callback", side_effect=fake_register), \
             patch("asyncio.sleep", new=AsyncMock()):
            devices = await manager.async_discover_devices("00:11:22:33:44:55")
            self.assertEqual(len(devices), 4)

            # Sort order: -35 dBm, -50 dBm, -85 dBm, None
            self.assertEqual(devices[0]["address"], "AA:BB:CC:DD:EE:01")
            self.assertEqual(devices[0]["name"], "Josh's iPhone")
            self.assertEqual(devices[0]["label"], "Josh's iPhone (AA:BB:CC:DD:EE:01) [-35 dBm]")
            self.assertEqual(devices[0]["rssi"], -35)

            self.assertEqual(devices[1]["address"], "AA:BB:CC:DD:EE:02")
            self.assertEqual(devices[1]["name"], "Apple Device")
            self.assertEqual(devices[1]["label"], "Apple Device (AA:BB:CC:DD:EE:02) [-50 dBm]")
            self.assertEqual(devices[1]["rssi"], -50)
            self.assertIn(76, devices[1]["manufacturer_data"])

            self.assertEqual(devices[2]["address"], "AA:BB:CC:DD:EE:03")
            self.assertEqual(devices[2]["name"], "AA:BB:CC:DD:EE:03")
            self.assertEqual(devices[2]["label"], "AA:BB:CC:DD:EE:03 [-85 dBm]")
            self.assertEqual(devices[2]["rssi"], -85)

            self.assertEqual(devices[3]["address"], "AA:BB:CC:DD:EE:04")
            self.assertEqual(devices[3]["name"], "Distant Device")
            self.assertEqual(devices[3]["label"], "Distant Device (AA:BB:CC:DD:EE:04)")
            self.assertIsNone(devices[3]["rssi"])



    async def test_adapter_pairing_mode_enable_disable(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        with patch.object(manager, "_async_apply_bluez_properties", new=AsyncMock()) as mock_apply:
            await manager.async_enable_pairing_mode("hci0", timeout=180)
            self.assertTrue(manager.adapter_properties["hci0"]["Discoverable"])
            self.assertTrue(manager.adapter_properties["hci0"]["Pairable"])
            self.assertEqual(manager.adapter_properties["hci0"]["DiscoverableTimeout"], 180)
            mock_apply.assert_called_with("hci0", discoverable=True, pairable=True, timeout=180)

            await manager.async_disable_pairing_mode("hci0")
            self.assertFalse(manager.adapter_properties["hci0"]["Discoverable"])
            self.assertFalse(manager.adapter_properties["hci0"]["Pairable"])
            self.assertEqual(manager.adapter_properties["hci0"]["DiscoverableTimeout"], 0)
            mock_apply.assert_called_with("hci0", discoverable=False, pairable=False, timeout=0)

    async def test_adapter_pairing_mode_timeout_auto_revert(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        with patch.object(manager, "_async_apply_bluez_properties", new=AsyncMock()):
            # Enable with 1 second timeout
            await manager.async_enable_pairing_mode("hci0", timeout=1)
            self.assertTrue(manager.adapter_properties["hci0"]["Discoverable"])
            # Let the background task run
            import asyncio
            await asyncio.sleep(1.05)
            self.assertFalse(manager.adapter_properties["hci0"]["Discoverable"])
            self.assertFalse(manager.adapter_properties["hci0"]["Pairable"])

if __name__ == "__main__":
    unittest.main()
