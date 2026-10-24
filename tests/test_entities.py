"""Unit tests for sensor, button, diagnostics, and integration init."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path
from datetime import datetime, timezone

# Setup mocks
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.mock_ha import setup_ha_mocks
setup_ha_mocks()

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.ble_irk_extractor.const import DOMAIN
from custom_components.ble_irk_extractor.coordinator import BleIrkCoordinator
from custom_components.ble_irk_extractor.sensor import BleIrkSensor, _unique_label, async_setup_entry as async_setup_sensor
from custom_components.ble_irk_extractor.button import BleIrkRescanButton, async_setup_entry as async_setup_button
from custom_components.ble_irk_extractor.diagnostics import async_get_config_entry_diagnostics
from custom_components.ble_irk_extractor.bluez import IrkRecord, ScanResult
from custom_components.ble_irk_extractor import async_setup_entry, async_unload_entry


class EntityAndDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    """Test entities, diagnostics, and setup/unload."""

    def test_unique_label(self) -> None:
        existing = {"Pixel": "irk1", "Pixel (2)": "irk2"}
        self.assertEqual(_unique_label("iPhone", existing), "iPhone")
        self.assertEqual(_unique_label("Pixel", existing), "Pixel (3)")

    def test_sensor_properties(self) -> None:
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="test_entry", title="My Extractor")
        coord = BleIrkCoordinator(hass, entry)

        sensor = BleIrkSensor(entry, coord)
        self.assertEqual(sensor.native_value, None)
        self.assertEqual(sensor.extra_state_attributes, {})

        now = datetime.now(timezone.utc)
        rec1 = IrkRecord("Phone", "AA:BB:CC:DD:EE:01", "0" * 32, "/config/bluez_irks", "/config/bluez_irks/1/info")
        rec2 = IrkRecord("Phone", "AA:BB:CC:DD:EE:02", "1" * 32, "/config/bluez_irks", "/config/bluez_irks/2/info")
        coord.data = ScanResult(
            records=(rec1, rec2),
            scanned_paths=("/config/bluez_irks",),
            missing_paths=("/var/lib/bluetooth",),
            errors=(),
            scanned_at=now,
        )

        self.assertEqual(sensor.native_value, 2)
        attrs = sensor.extra_state_attributes
        self.assertIn("Phone", attrs["keys"])
        self.assertIn("Phone (2)", attrs["keys"])
        self.assertEqual(attrs["keys"]["Phone"], "0" * 32)
        self.assertEqual(attrs["keys"]["Phone (2)"], "1" * 32)
        self.assertEqual(attrs["devices"]["Phone"]["address"], "AA:BB:CC:DD:EE:01")
        self.assertEqual(attrs["devices"]["Phone (2)"]["address"], "AA:BB:CC:DD:EE:02")
        self.assertEqual(attrs["sources"], ["/config/bluez_irks"])
        self.assertEqual(attrs["missing_paths"], ["/var/lib/bluetooth"])

    async def test_button_press(self) -> None:
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="test_entry", title="My Extractor")
        coord = BleIrkCoordinator(hass, entry)
        coord.async_refresh = AsyncMock()

        button = BleIrkRescanButton(entry, coord)
        await button.async_press()
        coord.async_refresh.assert_called_once()

    async def test_diagnostics_redacts_irks(self) -> None:
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="diag_entry", title="My Extractor", data={"scan_interval": 30})
        coord = BleIrkCoordinator(hass, entry)

        now = datetime.now(timezone.utc)
        rec = IrkRecord("Secret Phone", "AA:BB:CC:DD:EE:FF", "A" * 32, "/path", "/path/info")
        coord.data = ScanResult(
            records=(rec,),
            scanned_paths=("/path",),
            missing_paths=(),
            errors=(),
            scanned_at=now,
        )
        hass.data = {DOMAIN: {entry.entry_id: coord}}

        diag = await async_get_config_entry_diagnostics(hass, entry)
        self.assertEqual(diag["entry"]["title"], "My Extractor")
        self.assertEqual(diag["scan"]["record_count"], 1)
        self.assertEqual(diag["scan"]["records"][0]["name"], "Secret Phone")
        self.assertEqual(diag["scan"]["records"][0]["address"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(diag["scan"]["records"][0]["irk"], "**redacted**")
        self.assertNotIn("A" * 32, str(diag))

    async def test_platform_setup_callbacks(self) -> None:
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="setup_entry", title="My Extractor")
        coord = BleIrkCoordinator(hass, entry)
        hass.data = {DOMAIN: {entry.entry_id: coord}}

        mock_add_sensor = MagicMock()
        await async_setup_sensor(hass, entry, mock_add_sensor)
        mock_add_sensor.assert_called_once()
        self.assertIsInstance(mock_add_sensor.call_args[0][0][0], BleIrkSensor)

        mock_add_button = MagicMock()
        await async_setup_button(hass, entry, mock_add_button)
        mock_add_button.assert_called_once()
        self.assertIsInstance(mock_add_button.call_args[0][0][0], BleIrkRescanButton)

    async def test_integration_setup_and_unload(self) -> None:
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="integration_entry", title="My Extractor")

        with patch("custom_components.ble_irk_extractor.coordinator.scan_paths") as mock_scan:
            mock_scan.return_value = ScanResult((), (), (), (), datetime.now(timezone.utc))
            hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
            hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

            setup_ok = await async_setup_entry(hass, entry)
            self.assertTrue(setup_ok)
            self.assertIn(entry.entry_id, hass.data[DOMAIN])

            unload_ok = await async_unload_entry(hass, entry)
            self.assertTrue(unload_ok)
            self.assertNotIn(entry.entry_id, hass.data[DOMAIN])


if __name__ == "__main__":
    unittest.main()
