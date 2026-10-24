"""Unit tests for the BLE IRK Extractor config flow."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

# Setup mocks
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.mock_ha import setup_ha_mocks
setup_ha_mocks()

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.ble_irk_extractor.config_flow import (
    BleIrkExtractorConfigFlow,
    BleIrkExtractorOptionsFlow,
    _clean_data,
    _paths,
)
from custom_components.ble_irk_extractor.const import (
    CONF_COPIED_PATH,
    CONF_HOST_PATH,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SETUP_ANYWAY,
    DEFAULT_NAME,
    DOMAIN,
)
from custom_components.ble_irk_extractor.bluez import IrkRecord, ScanResult


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    """Test the Config and Options flows."""

    def test_clean_data_and_paths(self) -> None:
        data = _clean_data({
            CONF_NAME: "  My Custom Extractor  ",
            CONF_COPIED_PATH: " /config/test ",
            CONF_HOST_PATH: " ",
            CONF_SCAN_INTERVAL: "30",
        })
        self.assertEqual(data[CONF_NAME], "My Custom Extractor")
        self.assertEqual(data[CONF_COPIED_PATH], "/config/test")
        self.assertEqual(data[CONF_HOST_PATH], "")
        self.assertEqual(data[CONF_SCAN_INTERVAL], 30)

        paths = _paths(data)
        self.assertEqual(paths, ["/config/test"])

    async def test_step_user_menu(self) -> None:
        flow = BleIrkExtractorConfigFlow()

        # Display menu
        result = await flow.async_step_user()
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "user")

        # Action: configure_paths
        with patch.object(flow, "async_step_paths", new=AsyncMock(return_value={"type": "form", "step_id": "paths"})) as mock_paths:
            res = await flow.async_step_user({"action": "configure_paths"})
            mock_paths.assert_called_once()
            self.assertEqual(res["step_id"], "paths")

        # Action: pair_device
        with patch.object(flow, "async_step_pair_device", new=AsyncMock(return_value={"type": "form", "step_id": "pair_device"})) as mock_pair:
            res = await flow.async_step_user({"action": "pair_device"})
            mock_pair.assert_called_once()
            self.assertEqual(res["step_id"], "pair_device")

    async def test_step_paths_validation_and_creation(self) -> None:
        flow = BleIrkExtractorConfigFlow()

        # Empty paths error
        res_empty = await flow.async_step_paths({CONF_COPIED_PATH: "", CONF_HOST_PATH: ""})
        self.assertEqual(res_empty["errors"], {"base": "no_paths"})

        # Path not found error
        scan_not_found = ScanResult(records=(), scanned_paths=(), missing_paths=("/invalid",), errors=(), scanned_at=None)
        with patch("custom_components.ble_irk_extractor.config_flow.scan_paths", return_value=scan_not_found):
            res_not_found = await flow.async_step_paths({CONF_COPIED_PATH: "/invalid"})
            self.assertEqual(res_not_found["errors"], {"base": "path_not_found"})

        # Successful scan with records creates entry
        rec = IrkRecord("Phone", "AA:BB:CC:DD:EE:FF", "0" * 31 + "1", "/valid", "/valid/info")
        scan_success = ScanResult(records=(rec,), scanned_paths=("/valid",), missing_paths=(), errors=(), scanned_at=None)
        with patch("custom_components.ble_irk_extractor.config_flow.scan_paths", return_value=scan_success):
            res_success = await flow.async_step_paths({CONF_NAME: "Found Keys", CONF_COPIED_PATH: "/valid"})
            self.assertEqual(res_success["type"], "create_entry")
            self.assertEqual(res_success["title"], "Found Keys")

        # Scan with 0 records transitions to no_keys
        scan_no_keys = ScanResult(records=(), scanned_paths=("/valid",), missing_paths=(), errors=(), scanned_at=None)
        with patch("custom_components.ble_irk_extractor.config_flow.scan_paths", return_value=scan_no_keys):
            res_no_keys = await flow.async_step_paths({CONF_NAME: "Empty Keys", CONF_COPIED_PATH: "/valid"})
            self.assertEqual(res_no_keys["type"], "form")
            self.assertEqual(res_no_keys["step_id"], "no_keys")

    async def test_step_no_keys(self) -> None:
        flow = BleIrkExtractorConfigFlow()

        # Abort if no pending setup
        flow._pending_data = None
        res_abort = await flow.async_step_no_keys()
        self.assertEqual(res_abort["type"], "abort")
        self.assertEqual(res_abort["reason"], "no_pending_setup")

        flow._pending_data = {CONF_NAME: "Pending", CONF_COPIED_PATH: "/valid"}

        # Show no_keys form
        res_form = await flow.async_step_no_keys()
        self.assertEqual(res_form["type"], "form")
        self.assertEqual(res_form["step_id"], "no_keys")

        # Setup anyway -> True
        res_create = await flow.async_step_no_keys({CONF_SETUP_ANYWAY: True})
        self.assertEqual(res_create["type"], "create_entry")
        self.assertEqual(res_create["title"], "Pending")

        # Setup anyway -> False goes back to paths form
        with patch.object(flow, "async_step_paths", new=AsyncMock(return_value={"type": "form", "step_id": "paths"})) as mock_paths:
            res_retry = await flow.async_step_no_keys({CONF_SETUP_ANYWAY: False})
            mock_paths.assert_called_once_with(flow._pending_data)

    async def test_step_pair_device(self) -> None:
        flow = BleIrkExtractorConfigFlow()

        # No adapters -> abort
        with patch("custom_components.ble_irk_extractor.config_flow.BleIrkPairingManager.async_get_adapters", new=AsyncMock(return_value=[])):
            res_no_adapters = await flow.async_step_pair_device()
            self.assertEqual(res_no_adapters["type"], "abort")
            self.assertEqual(res_no_adapters["reason"], "no_adapters")

        # Adapters present -> show form with {address: label} mapping
        adapters = [{"address": "00:11:22:33:44:55", "name": "hci0 - Bluetooth Adapter (00:11:22:33:44:55)"}]
        with patch("custom_components.ble_irk_extractor.config_flow.BleIrkPairingManager.async_get_adapters", new=AsyncMock(return_value=adapters)):
            res_adapters = await flow.async_step_pair_device()
            self.assertEqual(res_adapters["type"], "form")
            self.assertEqual(res_adapters["step_id"], "pair_device")
            schema = res_adapters["data_schema"].schema
            self.assertEqual(
                schema["adapter_address"].container,
                {"00:11:22:33:44:55": "hci0 - Bluetooth Adapter (00:11:22:33:44:55)"},
            )

        # Adapter selected
        with patch.object(flow, "async_step_device_selection", new=AsyncMock(return_value={"type": "form", "step_id": "device_selection"})):
            res_select = await flow.async_step_pair_device({"adapter_address": "00:11:22:33:44:55"})
            self.assertEqual(flow._pairing_adapter, "00:11:22:33:44:55")
            self.assertEqual(res_select["step_id"], "device_selection")

    async def test_step_device_selection(self) -> None:
        flow = BleIrkExtractorConfigFlow()
        flow._pairing_adapter = "00:11:22:33:44:55"

        # Discovered devices returned with labels
        devices = [
            {
                "address": "AA:BB:CC:DD:EE:01",
                "name": "Josh's iPhone",
                "rssi": -50,
                "label": "Josh's iPhone (AA:BB:CC:DD:EE:01) [-50 dBm]",
            },
            {
                "address": "AA:BB:CC:DD:EE:02",
                "name": "Apple Device",
                "rssi": -65,
                "label": "Apple Device (AA:BB:CC:DD:EE:02) [-65 dBm]",
            },
        ]
        with patch("custom_components.ble_irk_extractor.config_flow.BleIrkPairingManager.async_discover_devices", new=AsyncMock(return_value=devices)):
            res_devices = await flow.async_step_device_selection()
            self.assertEqual(res_devices["type"], "form")
            self.assertEqual(res_devices["step_id"], "device_selection")
            # Verify {address: label} options in schema
            schema = res_devices["data_schema"].schema
            validator = schema["device_address"]
            self.assertEqual(
                validator.container,
                {
                    "AA:BB:CC:DD:EE:01": "Josh's iPhone (AA:BB:CC:DD:EE:01) [-50 dBm]",
                    "AA:BB:CC:DD:EE:02": "Apple Device (AA:BB:CC:DD:EE:02) [-65 dBm]",
                },
            )

        # Pick a device -> moves to pair_confirm with resolved name
        with patch.object(flow, "async_step_pair_confirm", new=AsyncMock(return_value={"type": "form", "step_id": "pair_confirm"})):
            res_pick = await flow.async_step_device_selection({"device_address": "AA:BB:CC:DD:EE:01"})
            self.assertEqual(flow._pairing_device, "AA:BB:CC:DD:EE:01")
            self.assertEqual(flow._pairing_device_name, "Josh's iPhone")
            self.assertEqual(res_pick["step_id"], "pair_confirm")

        # Pick Apple device -> sets name to Apple Device
        with patch.object(flow, "async_step_pair_confirm", new=AsyncMock(return_value={"type": "form", "step_id": "pair_confirm"})):
            res_pick2 = await flow.async_step_device_selection({"device_address": "AA:BB:CC:DD:EE:02"})
            self.assertEqual(flow._pairing_device, "AA:BB:CC:DD:EE:02")
            self.assertEqual(flow._pairing_device_name, "Apple Device")
            self.assertEqual(res_pick2["step_id"], "pair_confirm")

        # No devices found case
        with patch("custom_components.ble_irk_extractor.config_flow.BleIrkPairingManager.async_discover_devices", new=AsyncMock(return_value=[])):
            res_none = await flow.async_step_device_selection()
            self.assertEqual(res_none["type"], "form")
            self.assertEqual(res_none["errors"], {"base": "no_devices_found"})

    async def test_step_pair_confirm_and_pairing(self) -> None:
        flow = BleIrkExtractorConfigFlow()
        flow._pairing_device = "AA:BB:CC:DD:EE:01"
        flow._pairing_device_name = "Phone"

        # Show confirm form
        res_confirm_form = await flow.async_step_pair_confirm()
        self.assertEqual(res_confirm_form["type"], "form")
        self.assertEqual(res_confirm_form["step_id"], "pair_confirm")

        # Confirm pairing fails -> transitions to pairing_failed form
        with patch("custom_components.ble_irk_extractor.config_flow.BleIrkPairingManager.async_pair_device", new=AsyncMock(return_value=False)):
            res_fail = await flow.async_step_pairing()
            self.assertEqual(res_fail["type"], "form")
            self.assertEqual(res_fail["step_id"], "pairing_failed")

        # Confirm pairing exception -> handled safely and transitions to pairing_failed
        with patch("custom_components.ble_irk_extractor.config_flow.BleIrkPairingManager.async_pair_device", new=AsyncMock(side_effect=RuntimeError("D-Bus down"))):
            res_exc = await flow.async_step_pairing()
            self.assertEqual(res_exc["type"], "form")
            self.assertEqual(res_exc["step_id"], "pairing_failed")

        # Confirm pairing succeeds and finds IRK
        rec = IrkRecord("Phone", "AA:BB:CC:DD:EE:01", "0" * 31 + "1", "/var/lib/bluetooth", "/var/lib/bluetooth/info")
        with patch("custom_components.ble_irk_extractor.config_flow.BleIrkPairingManager.async_pair_device", new=AsyncMock(return_value=True)),              patch("custom_components.ble_irk_extractor.config_flow.BleIrkPairingManager.async_wait_for_bond_record", new=AsyncMock(return_value=rec)):
            res_pair_ok = await flow.async_step_pairing()
            self.assertEqual(res_pair_ok["type"], "form")
            self.assertEqual(res_pair_ok["step_id"], "pairing_result")
            self.assertEqual(flow._last_irk_record, rec)

    async def test_step_pairing_failed_recovery(self) -> None:
        flow = BleIrkExtractorConfigFlow()
        flow._pairing_device = "AA:BB:CC:DD:EE:01"
        flow._pairing_device_name = "Josh's iPhone"

        # Show recovery form
        res_form = await flow.async_step_pairing_failed()
        self.assertEqual(res_form["type"], "form")
        self.assertEqual(res_form["step_id"], "pairing_failed")
        schema = res_form["data_schema"].schema
        self.assertIn("recovery_action", schema)

        # Recovery action: retry_selection
        with patch.object(flow, "async_step_device_selection", new=AsyncMock(return_value={"type": "form", "step_id": "device_selection"})) as mock_select:
            res_retry = await flow.async_step_pairing_failed({"recovery_action": "retry_selection"})
            mock_select.assert_called_once()
            self.assertEqual(res_retry["step_id"], "device_selection")

        # Recovery action: configure_paths
        with patch.object(flow, "async_step_paths", new=AsyncMock(return_value={"type": "form", "step_id": "paths"})) as mock_paths:
            res_paths = await flow.async_step_pairing_failed({"recovery_action": "configure_paths"})
            mock_paths.assert_called_once()
            self.assertEqual(res_paths["step_id"], "paths")

    async def test_step_pairing_result(self) -> None:
        flow = BleIrkExtractorConfigFlow()
        rec = IrkRecord("Phone", "AA:BB:CC:DD:EE:01", "0" * 31 + "1", "/var/lib/bluetooth", "/var/lib/bluetooth/info")
        flow._last_irk_record = rec

        # Initial entry creation with create_pbd
        with patch.object(flow.hass.config_entries.flow, "async_init", new=AsyncMock()) as mock_pbd:
            res_entry = await flow.async_step_pairing_result({"create_pbd": True})
            mock_pbd.assert_called_once()
            self.assertEqual(res_entry["type"], "create_entry")
            self.assertEqual(res_entry["title"], DEFAULT_NAME)

    async def test_reconfigure_and_options_flow(self) -> None:
        # Reconfigure existing entry
        flow = BleIrkExtractorConfigFlow()
        entry = ConfigEntry(data={CONF_NAME: "Original", CONF_COPIED_PATH: "/valid"})
        flow.context = {"entry_id": "test_entry"}
        flow.hass.config_entries.async_get_entry = MagicMock(return_value=entry)

        # Successful reconfigure
        scan_ok = ScanResult(records=(), scanned_paths=("/valid",), missing_paths=(), errors=(), scanned_at=None)
        with patch("custom_components.ble_irk_extractor.config_flow.scan_paths", return_value=scan_ok):
            res_reconfig = await flow.async_step_reconfigure({CONF_NAME: "Updated", CONF_COPIED_PATH: "/valid"})
            self.assertEqual(res_reconfig["type"], "abort")
            self.assertEqual(res_reconfig["reason"], "reconfigure_successful")
            self.assertEqual(entry.data[CONF_NAME], "Updated")

        # Options flow
        opt_flow = BleIrkExtractorOptionsFlow(entry)
        with patch("custom_components.ble_irk_extractor.config_flow.scan_paths", return_value=scan_ok):
            res_opt = await opt_flow.async_step_init({CONF_NAME: "Opt Updated", CONF_COPIED_PATH: "/valid"})
            self.assertEqual(res_opt["type"], "create_entry")
            self.assertEqual(entry.data[CONF_NAME], "Opt Updated")


if __name__ == "__main__":
    unittest.main()
