"""Unit tests for BleIrkSensor diagnostic entity."""

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
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from custom_components.ble_irk_extractor.const import DOMAIN
from custom_components.ble_irk_extractor.coordinator import BleIrkCoordinator
from custom_components.ble_irk_extractor.sensor import (
    BleIrkSensor,
    _unique_label,
    async_setup_entry as async_setup_sensor,
)
from custom_components.ble_irk_extractor.bluez import IrkRecord, ScanResult


class SensorPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Validate BleIrkSensor entity state, attributes, and lifecycle."""

    def test_state_and_count_handling_across_0_1_n(self) -> None:
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="sensor_test_entry", title="IRK Sensor Test")
        coord = BleIrkCoordinator(hass, entry)
        sensor = BleIrkSensor(entry, coord)

        # Before scan / data is None
        self.assertIsNone(sensor.native_value)
        self.assertEqual(sensor.extra_state_attributes, {})

        # 0 keys discovered
        coord.data = ScanResult(records=(), scanned_paths=("/config/bluez_irks",), missing_paths=(), errors=(), scanned_at=datetime.now(timezone.utc))
        self.assertEqual(sensor.native_value, 0)
        self.assertEqual(sensor.extra_state_attributes["keys"], {})
        self.assertEqual(sensor.extra_state_attributes["devices"], {})
        self.assertEqual(sensor.extra_state_attributes["extracted_devices"], [])

        # 1 key discovered
        rec1 = IrkRecord("Phone 1", "AA:BB:CC:DD:EE:01", "1" * 32, "/config/bluez_irks", "/config/bluez_irks/1/info")
        coord.data = ScanResult(records=(rec1,), scanned_paths=("/config/bluez_irks",), missing_paths=(), errors=(), scanned_at=datetime.now(timezone.utc))
        self.assertEqual(sensor.native_value, 1)
        self.assertEqual(len(sensor.extra_state_attributes["extracted_devices"]), 1)

        # N (3) keys discovered
        rec2 = IrkRecord("Phone 2", "AA:BB:CC:DD:EE:02", "2" * 32, "/config/bluez_irks", "/config/bluez_irks/2/info")
        rec3 = IrkRecord("Phone 3", "AA:BB:CC:DD:EE:03", "3" * 32, "/config/bluez_irks", "/config/bluez_irks/3/info")
        coord.data = ScanResult(records=(rec1, rec2, rec3), scanned_paths=("/config/bluez_irks",), missing_paths=(), errors=(), scanned_at=datetime.now(timezone.utc))
        self.assertEqual(sensor.native_value, 3)
        self.assertEqual(len(sensor.extra_state_attributes["extracted_devices"]), 3)

    def test_attribute_schema_and_robustness(self) -> None:
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="attr_entry", title="Attribute Test")
        coord = BleIrkCoordinator(hass, entry)
        sensor = BleIrkSensor(entry, coord)

        rec1 = IrkRecord(
            name="Josh Pixel 9",
            address="AA:BB:CC:DD:EE:11",
            irk="A1" * 16,
            source="/config/bluez_irks",
            path="/config/bluez_irks/11/info",
        )
        rec2 = IrkRecord(
            name="Josh Pixel 9",  # Duplicate name
            address="AA:BB:CC:DD:EE:22",
            irk="B2" * 16,
            source="/config/bluez_irks",
            path="/config/bluez_irks/22/info",
        )
        rec_edge = IrkRecord(
            name="",  # Empty name fallback
            address="",  # Empty address fallback
            irk="C3" * 16,
            source="/config/bluez_irks",
            path="/config/bluez_irks/33/info",
        )

        coord.data = ScanResult(
            records=(rec1, rec2, rec_edge),
            scanned_paths=("/config/bluez_irks",),
            missing_paths=("/missing/dir",),
            errors=("Could not read /bad/path",),
            scanned_at=None,  # Missing timestamp edge case
        )

        attrs = sensor.extra_state_attributes

        # Schema keys exist
        self.assertIn("keys", attrs)
        self.assertIn("devices", attrs)
        self.assertIn("extracted_devices", attrs)
        self.assertIn("sources", attrs)
        self.assertIn("missing_paths", attrs)
        self.assertIn("errors", attrs)
        self.assertIsNone(attrs["last_scan"])

        # Structured attributes verification
        devices = attrs["devices"]
        self.assertIn("Josh Pixel 9", devices)
        self.assertIn("Josh Pixel 9 (2)", devices)

        dev1 = devices["Josh Pixel 9"]
        self.assertEqual(dev1["friendly_name"], "Josh Pixel 9")
        self.assertEqual(dev1["mac_address"], "AA:BB:CC:DD:EE:11")
        self.assertEqual(dev1["address"], "AA:BB:CC:DD:EE:11")
        self.assertEqual(dev1["irk"], "A1" * 16)

        # Extracted devices list contains all records
        extracted = attrs["extracted_devices"]
        self.assertEqual(len(extracted), 3)
        self.assertEqual(extracted[0]["friendly_name"], "Josh Pixel 9")
        self.assertEqual(extracted[0]["mac_address"], "AA:BB:CC:DD:EE:11")
        self.assertEqual(extracted[1]["friendly_name"], "Josh Pixel 9")
        self.assertEqual(extracted[1]["mac_address"], "AA:BB:CC:DD:EE:22")
        self.assertEqual(extracted[2]["friendly_name"], "Unknown device")
        self.assertEqual(extracted[2]["mac_address"], "")

    def test_diagnostic_category_and_unique_id(self) -> None:
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="unique_entry_123", title="BLE IRK Extractor")
        coord = BleIrkCoordinator(hass, entry)
        sensor = BleIrkSensor(entry, coord)

        self.assertEqual(sensor.unique_id, "unique_entry_123_irk_keys")
        self.assertEqual(sensor.entity_category, EntityCategory.DIAGNOSTIC)
        self.assertEqual(sensor.translation_key, "irk_keys")
        self.assertEqual(sensor.icon, "mdi:bluetooth")

    async def test_sensor_setup_entry(self) -> None:
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="setup_entry_456", title="BLE IRK Extractor")
        coord = BleIrkCoordinator(hass, entry)
        hass.data = {DOMAIN: {entry.entry_id: coord}}

        added_entities = []
        def mock_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_sensor(hass, entry, mock_add_entities)
        self.assertEqual(len(added_entities), 1)
        self.assertIsInstance(added_entities[0], BleIrkSensor)


if __name__ == "__main__":
    unittest.main()
