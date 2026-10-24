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
)
from custom_components.ble_irk_extractor.bluez import IrkRecord, ScanResult


class PairingTests(unittest.IsolatedAsyncioTestCase):
    """Test BleIrkPairingManager discovery, pairing, and extraction."""

    async def test_async_get_adapters(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        mock_adapter = MagicMock()
        mock_adapter.address = "00:11:22:33:44:55"
        mock_adapter.name = "hci0"

        with patch.object(
            bluetooth, "async_get_adapters", new=AsyncMock(return_value=[mock_adapter])
        ):
            adapters = await manager.async_get_adapters()
            self.assertEqual(len(adapters), 1)
            self.assertEqual(adapters[0]["address"], "00:11:22:33:44:55")
            self.assertEqual(adapters[0]["name"], "hci0")

    async def test_async_get_adapters_sync_fallback(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        mock_adapter = MagicMock()
        mock_adapter.address = "00:11:22:33:44:55"
        mock_adapter.name = None

        def sync_get_adapters(h):
            return [mock_adapter]

        with patch.object(
            bluetooth, "async_get_adapters", side_effect=TypeError("sync function")
        ), patch.object(hass, "async_add_executor_job", side_effect=lambda fn, *args: sync_get_adapters(hass)):
            adapters = await manager.async_get_adapters()
            self.assertEqual(len(adapters), 1)
            self.assertEqual(adapters[0]["address"], "00:11:22:33:44:55")
            self.assertEqual(adapters[0]["name"], "00:11:22:33:44:55")

    async def test_async_get_adapters_error(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        with patch.object(
            bluetooth, "async_get_adapters", side_effect=RuntimeError("Bluetooth daemon down")
        ):
            adapters = await manager.async_get_adapters()
            self.assertEqual(adapters, [])

    async def test_async_discover_devices(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        mock_scanner = MagicMock()
        unsub = MagicMock()

        def fake_register(hass, callback, matcher, scanning_mode):
            # Simulate devices discovered
            callback(BluetoothServiceInfoBleak("AA:BB:CC:DD:EE:01", name="Device Low", rssi=-80), None)
            callback(BluetoothServiceInfoBleak("AA:BB:CC:DD:EE:02", name="Device High", rssi=-40), None)
            return unsub

        with patch.object(bluetooth, "async_get_scanner", return_value=mock_scanner),              patch.object(bluetooth, "async_register_scanner", side_effect=fake_register),              patch("asyncio.sleep", new=AsyncMock()):
            devices = await manager.async_discover_devices("00:11:22:33:44:55")
            self.assertEqual(len(devices), 2)
            # Verified sorted by RSSI descending
            self.assertEqual(devices[0]["address"], "AA:BB:CC:DD:EE:02")
            self.assertEqual(devices[0]["rssi"], -40)
            self.assertEqual(devices[1]["address"], "AA:BB:CC:DD:EE:01")
            self.assertEqual(devices[1]["rssi"], -80)
            unsub.assert_called_once()

    async def test_async_discover_devices_no_scanner(self) -> None:
        hass = HomeAssistant()
        manager = BleIrkPairingManager(hass)

        with patch.object(bluetooth, "async_get_scanner", return_value=None):
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
        with patch.object(manager, "extract_irk_for_device", side_effect=lambda *args: calls.pop(0)),              patch("asyncio.sleep", new=AsyncMock()):
            result = await manager.async_wait_for_bond_record("AA:BB:CC:DD:EE:FF", ["/config/bluez_irks"])
            self.assertEqual(result, rec)

        # Timeout / not found
        with patch.object(manager, "extract_irk_for_device", return_value=None),              patch("asyncio.sleep", new=AsyncMock()):
            result = await manager.async_wait_for_bond_record("AA:BB:CC:DD:EE:FF", ["/config/bluez_irks"])
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
