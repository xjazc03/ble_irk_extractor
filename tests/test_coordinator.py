"""Unit tests for the BLE IRK coordinator."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

# Setup mocks
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.mock_ha import setup_ha_mocks
setup_ha_mocks()

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ble_irk_extractor.coordinator import (
    BleIrkCoordinator,
    paths_from_entry,
)
from custom_components.ble_irk_extractor.const import (
    CONF_COPIED_PATH,
    CONF_HOST_PATH,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from custom_components.ble_irk_extractor.bluez import ScanResult


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    """Test the BleIrkCoordinator and path helper functions."""

    def test_paths_from_entry(self) -> None:
        # Both paths configured
        entry = ConfigEntry(
            data={
                CONF_COPIED_PATH: "/config/bluez_irks",
                CONF_HOST_PATH: "/var/lib/bluetooth",
            }
        )
        self.assertEqual(
            paths_from_entry(entry),
            ["/config/bluez_irks", "/var/lib/bluetooth"],
        )

        # Empty / whitespace paths ignored
        entry_empty = ConfigEntry(
            data={CONF_COPIED_PATH: "  ", CONF_HOST_PATH: ""}
        )
        self.assertEqual(paths_from_entry(entry_empty), [])

        # Deduplication
        entry_dup = ConfigEntry(
            data={
                CONF_COPIED_PATH: "/var/lib/bluetooth",
                CONF_HOST_PATH: "/var/lib/bluetooth",
            }
        )
        self.assertEqual(paths_from_entry(entry_dup), ["/var/lib/bluetooth"])

    def test_scan_interval_clamping(self) -> None:
        hass = HomeAssistant()

        # Valid custom interval
        entry = ConfigEntry(data={CONF_SCAN_INTERVAL: 15})
        coord = BleIrkCoordinator(hass, entry)
        self.assertEqual(coord.scan_interval_minutes, 15)

        # Value below minimum is clamped to MIN_SCAN_INTERVAL
        entry_low = ConfigEntry(data={CONF_SCAN_INTERVAL: 0})
        coord_low = BleIrkCoordinator(hass, entry_low)
        self.assertEqual(coord_low.scan_interval_minutes, MIN_SCAN_INTERVAL)

        # Value above maximum is clamped to MAX_SCAN_INTERVAL
        entry_high = ConfigEntry(data={CONF_SCAN_INTERVAL: 5000})
        coord_high = BleIrkCoordinator(hass, entry_high)
        self.assertEqual(coord_high.scan_interval_minutes, MAX_SCAN_INTERVAL)

        # Non-numeric value defaults to DEFAULT_SCAN_INTERVAL
        entry_invalid = ConfigEntry(data={CONF_SCAN_INTERVAL: "invalid"})
        coord_invalid = BleIrkCoordinator(hass, entry_invalid)
        self.assertEqual(coord_invalid.scan_interval_minutes, DEFAULT_SCAN_INTERVAL)

    async def test_async_update_data_success(self) -> None:
        hass = HomeAssistant()
        entry = ConfigEntry(
            data={CONF_COPIED_PATH: "/test/path"}
        )
        coord = BleIrkCoordinator(hass, entry)

        mock_scan_result = MagicMock(spec=ScanResult)
        with patch(
            "custom_components.ble_irk_extractor.coordinator.scan_paths",
            return_value=mock_scan_result,
        ):
            data = await coord._async_update_data()
            self.assertEqual(data, mock_scan_result)

    async def test_async_update_data_failure(self) -> None:
        hass = HomeAssistant()
        entry = ConfigEntry(data={CONF_COPIED_PATH: "/test/path"})
        coord = BleIrkCoordinator(hass, entry)

        with patch(
            "custom_components.ble_irk_extractor.coordinator.scan_paths",
            side_effect=PermissionError("Permission denied"),
        ):
            with self.assertRaises(UpdateFailed) as ctx:
                await coord._async_update_data()
            self.assertIn("Permission denied", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
