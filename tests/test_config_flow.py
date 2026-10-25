"""Unit tests for the BLE IRK Extractor config flow and options flow."""

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
    COMMAND_COPY_BLUEZ,
    CONF_COPIED_PATH,
    CONF_HOST_PATH,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SETUP_ANYWAY,
    DEFAULT_COPIED_PATH,
    DEFAULT_HOST_PATH,
    DEFAULT_NAME,
    DEFAULT_PAIRING_TIMEOUT,
    DOMAIN,
)
from custom_components.ble_irk_extractor.bluez import IrkRecord, ScanResult


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    """Test BleIrkExtractorConfigFlow 3-path architecture, validation, and options."""

    def setUp(self) -> None:
        super().setUp()
        self._patch_apply = patch(
            "custom_components.ble_irk_extractor.pairing.BleIrkPairingManager._async_apply_bluez_properties",
            new=AsyncMock(),
        )
        self._patch_apply.start()

    def tearDown(self) -> None:
        self._patch_apply.stop()
        super().tearDown()

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

        paths = _paths(data)
        self.assertEqual(paths, ["/config/test"])

    async def test_step_user_menu_and_routing(self) -> None:
        flow = BleIrkExtractorConfigFlow()

        # Initial view shows action selection
        res = await flow.async_step_user()
        self.assertEqual(res["type"], "form")
        self.assertEqual(res["step_id"], "user")

        # Routing to Path 1: select_irk
        with patch.object(
            flow, "async_step_select_irk", new=AsyncMock(return_value={"type": "form", "step_id": "select_irk"})
        ) as mock_p1:
            res_p1 = await flow.async_step_user({"action": "select_irk"})
            self.assertEqual(res_p1["step_id"], "select_irk")
            mock_p1.assert_called_once()

        # Routing to Path 2: add_irk
        with patch.object(
            flow, "async_step_add_irk", new=AsyncMock(return_value={"type": "form", "step_id": "add_irk"})
        ) as mock_p2:
            res_p2 = await flow.async_step_user({"action": "add_irk"})
            self.assertEqual(res_p2["step_id"], "add_irk")
            mock_p2.assert_called_once()

        # Routing to Path 3: extract_irk
        with patch.object(
            flow, "async_step_extract_irk", new=AsyncMock(return_value={"type": "form", "step_id": "extract_irk"})
        ) as mock_p3:
            res_p3 = await flow.async_step_user({"action": "extract_irk"})
            self.assertEqual(res_p3["step_id"], "extract_irk")
            mock_p3.assert_called_once()

    # ------------------------------------------------------------------
    # Path 1: Select IRK Key Tests & Key Details Card
    # ------------------------------------------------------------------

    async def test_step_select_irk_empty_shows_copy_command(self) -> None:
        flow = BleIrkExtractorConfigFlow()
        empty_scan = ScanResult(records=(), scanned_paths=(), missing_paths=(), errors=(), scanned_at=None)

        with patch("custom_components.ble_irk_extractor.config_flow.scan_paths", return_value=empty_scan):
            res = await flow.async_step_select_irk()
            self.assertEqual(res["type"], "form")
            self.assertEqual(res["step_id"], "select_irk")
            self.assertEqual(res["errors"], {"base": "no_irks_found"})
            self.assertEqual(res["description_placeholders"]["copy_command"], COMMAND_COPY_BLUEZ)

    async def test_step_select_irk_recency_sorting_and_handoff(self) -> None:
        flow = BleIrkExtractorConfigFlow()

        rec_old = IrkRecord(
            name="Older Phone",
            address="AA:BB:CC:DD:EE:01",
            irk="11111111111111111111111111111111",
            source="/config/bluez_irks",
            path="/config/bluez_irks/info1",
            mtime=1000.0,
        )
        rec_new = IrkRecord(
            name="Newest Phone",
            address="AA:BB:CC:DD:EE:02",
            irk="22222222222222222222222222222222",
            source="/config/bluez_irks",
            path="/config/bluez_irks/info2",
            mtime=2000.0,
        )
        scan_res = ScanResult(
            records=(rec_old, rec_new),
            scanned_paths=("/config/bluez_irks",),
            missing_paths=(),
            errors=(),
            scanned_at=None,
        )

        with patch("custom_components.ble_irk_extractor.config_flow.scan_paths", return_value=scan_res):
            # Form displays options, sorted by mtime descending, pre-selecting newest
            res_form = await flow.async_step_select_irk()
            self.assertEqual(res_form["type"], "form")
            self.assertEqual(res_form["step_id"], "select_irk")
            self.assertEqual(flow._scanned_irk_records[0], rec_new)

            # Selecting device transitions to Key Details Card
            res_details = await flow.async_step_select_irk({"selected_device": rec_new.irk})
            self.assertEqual(res_details["type"], "form")
            self.assertEqual(res_details["step_id"], "irk_details")
            self.assertEqual(res_details["description_placeholders"]["irk"], rec_new.irk)
            self.assertEqual(res_details["description_placeholders"]["name"], "Newest Phone")
            self.assertEqual(res_details["description_placeholders"]["address"], "AA:BB:CC:DD:EE:02")

            # Submitting Key Details Card with forward=True triggers private_ble_device and creates entry
            with patch.object(flow.hass.config_entries.flow, "async_init", new=AsyncMock()) as mock_pbd:
                res_create = await flow.async_step_irk_details({"forward_to_pbd": True})
                self.assertEqual(res_create["type"], "create_entry")
                mock_pbd.assert_called_once_with(
                    "private_ble_device",
                    context={"source": "user"},
                    data={
                        "address": "AA:BB:CC:DD:EE:02",
                        "irk": "22222222222222222222222222222222",
                        "name": "Newest Phone",
                    },
                )

    async def test_step_irk_details_inspect_only_does_not_forward(self) -> None:
        flow = BleIrkExtractorConfigFlow()
        rec = IrkRecord(
            name="Inspect Phone",
            address="AA:BB:CC:DD:EE:03",
            irk="33333333333333333333333333333333",
            source="/config/bluez_irks",
            path="/config/bluez_irks/info3",
        )
        flow._selected_record = rec

        with patch.object(flow.hass.config_entries.flow, "async_init", new=AsyncMock()) as mock_pbd:
            res_finish = await flow.async_step_irk_details({"forward_to_pbd": False})
            self.assertEqual(res_finish["type"], "create_entry")
            mock_pbd.assert_not_called()

    # ------------------------------------------------------------------
    # Path 2: Add IRK Key Tests (Validation & Sanitization)
    # ------------------------------------------------------------------

    async def test_step_add_irk_validation_errors(self) -> None:
        flow = BleIrkExtractorConfigFlow()

        # Non-hex characters
        res1 = await flow.async_step_add_irk({"name": "Device", "irk": "ZZZZ1111222233334444555566667777"})
        self.assertEqual(res1["errors"]["irk"], "invalid_irk_format")

        # Wrong length (less than 32 hex chars)
        res2 = await flow.async_step_add_irk({"name": "Device", "irk": "1234ABCD"})
        self.assertEqual(res2["errors"]["irk"], "invalid_irk_format")

        # All zeros
        res3 = await flow.async_step_add_irk({"name": "Device", "irk": "0" * 32})
        self.assertEqual(res3["errors"]["irk"], "invalid_irk_format")

    async def test_step_add_irk_sanitization_and_handoff(self) -> None:
        flow = BleIrkExtractorConfigFlow()

        raw_irk = " 01:23:45:67-89:ab:cd:ef 01:23:45:67-89:ab:cd:ef "
        expected_irk = "0123456789ABCDEF0123456789ABCDEF"

        with patch.object(flow.hass.config_entries.flow, "async_init", new=AsyncMock()) as mock_pbd:
            res = await flow.async_step_add_irk({"name": "My Pixel", "irk": raw_irk})
            self.assertEqual(res["type"], "create_entry")
            mock_pbd.assert_called_once_with(
                "private_ble_device",
                context={"source": "user"},
                data={
                    "address": "",
                    "irk": expected_irk,
                    "name": "My Pixel",
                },
            )

    # ------------------------------------------------------------------
    # Path 3: Extract IRK Key Tests (Adapter Safety & Decoupled Sync)
    # ------------------------------------------------------------------

    async def test_step_extract_irk_pairing_mode_and_decouple(self) -> None:
        flow = BleIrkExtractorConfigFlow()
        manager = flow._get_pairing_manager()

        with patch.object(manager, "async_get_adapters", new=AsyncMock(return_value=[{"address": "hci0", "name": "hci0"}])),              patch.object(manager, "async_enable_pairing_mode", new=AsyncMock()) as mock_enable,              patch.object(manager, "async_disable_pairing_mode", new=AsyncMock()) as mock_disable:

            # 1. Step extract_irk enables pairing mode
            res_extract = await flow.async_step_extract_irk()
            self.assertEqual(res_extract["type"], "form")
            self.assertEqual(res_extract["step_id"], "extract_irk")
            mock_enable.assert_called_once_with("hci0", timeout=DEFAULT_PAIRING_TIMEOUT)

            # 2. Confirming pairing disables pairing mode and advances to extract_sync
            res_confirm = await flow.async_step_extract_irk({"confirm_paired": True})
            self.assertEqual(res_confirm["type"], "form")
            self.assertEqual(res_confirm["step_id"], "extract_sync")
            self.assertEqual(res_confirm["description_placeholders"]["copy_command"], COMMAND_COPY_BLUEZ)
            mock_disable.assert_called_with("hci0")

            # 3. extract_sync completes setup WITHOUT calling private_ble_device
            with patch.object(flow.hass.config_entries.flow, "async_init", new=AsyncMock()) as mock_pbd:
                res_finish = await flow.async_step_extract_sync({})
                self.assertEqual(res_finish["type"], "create_entry")
                # Zero downstream calls to private_ble_device
                mock_pbd.assert_not_called()

    async def test_flow_abort_safely_disables_pairing_mode(self) -> None:
        flow = BleIrkExtractorConfigFlow()
        manager = flow._get_pairing_manager()

        # Simulate pairing mode active
        manager.set_adapter_pairing_mode("hci0", enabled=True, timeout=180)
        self.assertTrue(manager.adapter_properties["hci0"]["Discoverable"])
        flow._pairing_adapter = "hci0"

        # Abort flow
        res_abort = flow.async_abort(reason="user_canceled")
        self.assertEqual(res_abort["type"], "abort")
        self.assertEqual(res_abort["reason"], "user_canceled")

        # Invariant: adapter pairing mode is strictly reverted to False
        self.assertFalse(manager.adapter_properties["hci0"]["Discoverable"])
        self.assertFalse(manager.adapter_properties["hci0"]["Pairable"])

    # ------------------------------------------------------------------
    # Reconfiguration & Options Flow Tests
    # ------------------------------------------------------------------

    async def test_reconfigure_step(self) -> None:
        flow = BleIrkExtractorConfigFlow()
        entry = ConfigEntry(data={CONF_NAME: "Original", CONF_COPIED_PATH: "/valid"})
        flow.context = {"entry_id": "test_entry"}
        flow.hass.config_entries.async_get_entry = MagicMock(return_value=entry)

        res_form = await flow.async_step_reconfigure()
        self.assertEqual(res_form["type"], "form")
        self.assertEqual(res_form["step_id"], "reconfigure")

    async def test_options_flow_menu_routing(self) -> None:
        entry = ConfigEntry(entry_id="test_opt", data={CONF_NAME: "Options Extractor"})
        opt_flow = BleIrkExtractorOptionsFlow(entry)

        # Show options menu
        res_init = await opt_flow.async_step_init()
        self.assertEqual(res_init["type"], "form")
        self.assertEqual(res_init["step_id"], "init")

        # Route to select_irk
        with patch.object(
            opt_flow, "async_step_select_irk", new=AsyncMock(return_value={"type": "form", "step_id": "select_irk"})
        ) as mock_p1:
            res_p1 = await opt_flow.async_step_init({"action": "select_irk"})
            self.assertEqual(res_p1["step_id"], "select_irk")
            mock_p1.assert_called_once()

        # Route to add_irk
        with patch.object(
            opt_flow, "async_step_add_irk", new=AsyncMock(return_value={"type": "form", "step_id": "add_irk"})
        ) as mock_p2:
            res_p2 = await opt_flow.async_step_init({"action": "add_irk"})
            self.assertEqual(res_p2["step_id"], "add_irk")
            mock_p2.assert_called_once()

        # Route to extract_irk
        with patch.object(
            opt_flow, "async_step_extract_irk", new=AsyncMock(return_value={"type": "form", "step_id": "extract_irk"})
        ) as mock_p3:
            res_p3 = await opt_flow.async_step_init({"action": "extract_irk"})
            self.assertEqual(res_p3["step_id"], "extract_irk")
            mock_p3.assert_called_once()

        # Route to paths
        with patch.object(
            opt_flow, "async_step_paths", new=AsyncMock(return_value={"type": "form", "step_id": "paths"})
        ) as mock_paths:
            res_paths = await opt_flow.async_step_init({"action": "paths"})
            self.assertEqual(res_paths["step_id"], "paths")
            mock_paths.assert_called_once()

    async def test_options_flow_path1_select_details_and_forward(self) -> None:
        entry = ConfigEntry(entry_id="test_opt", data={CONF_NAME: "Options Extractor"})
        opt_flow = BleIrkExtractorOptionsFlow(entry)

        rec = IrkRecord(
            name="Opt Phone",
            address="AA:BB:CC:DD:EE:99",
            irk="99999999999999999999999999999999",
            source="/config/bluez_irks",
            path="/config/bluez_irks/info99",
        )
        scan_res = ScanResult(records=(rec,), scanned_paths=("/config/bluez_irks",), missing_paths=(), errors=(), scanned_at=None)

        with patch("custom_components.ble_irk_extractor.config_flow.scan_paths", return_value=scan_res):
            res_form = await opt_flow.async_step_select_irk()
            self.assertEqual(res_form["step_id"], "select_irk")

            # Selection leads to irk_details card
            res_details = await opt_flow.async_step_select_irk({"selected_device": rec.irk})
            self.assertEqual(res_details["step_id"], "irk_details")
            self.assertEqual(res_details["description_placeholders"]["irk"], rec.irk)
            self.assertEqual(res_details["description_placeholders"]["name"], "Opt Phone")

            # Forwarding to PBD creates empty options entry
            with patch.object(opt_flow.hass.config_entries.flow, "async_init", new=AsyncMock()) as mock_pbd:
                res_done = await opt_flow.async_step_irk_details({"forward_to_pbd": True})
                self.assertEqual(res_done["type"], "create_entry")
                self.assertEqual(res_done["data"], {})
                mock_pbd.assert_called_once_with(
                    "private_ble_device",
                    context={"source": "user"},
                    data={
                        "address": "AA:BB:CC:DD:EE:99",
                        "irk": "99999999999999999999999999999999",
                        "name": "Opt Phone",
                    },
                )

    async def test_options_flow_path1_inspect_only(self) -> None:
        entry = ConfigEntry(entry_id="test_opt", data={CONF_NAME: "Options Extractor"})
        opt_flow = BleIrkExtractorOptionsFlow(entry)
        rec = IrkRecord(
            name="Opt Phone",
            address="AA:BB:CC:DD:EE:99",
            irk="99999999999999999999999999999999",
            source="/config/bluez_irks",
            path="/config/bluez_irks/info99",
        )
        opt_flow._selected_record = rec

        with patch.object(opt_flow.hass.config_entries.flow, "async_init", new=AsyncMock()) as mock_pbd:
            res_done = await opt_flow.async_step_irk_details({"forward_to_pbd": False})
            self.assertEqual(res_done["type"], "create_entry")
            self.assertEqual(res_done["data"], {})
            mock_pbd.assert_not_called()

    async def test_options_flow_path2_add_irk(self) -> None:
        entry = ConfigEntry(entry_id="test_opt", data={CONF_NAME: "Options Extractor"})
        opt_flow = BleIrkExtractorOptionsFlow(entry)

        with patch.object(opt_flow.hass.config_entries.flow, "async_init", new=AsyncMock()) as mock_pbd:
            res = await opt_flow.async_step_add_irk({"name": "Manual Opt", "irk": "A" * 32})
            self.assertEqual(res["type"], "create_entry")
            self.assertEqual(res["data"], {})
            mock_pbd.assert_called_once()

    async def test_options_flow_path3_extract_and_sync(self) -> None:
        entry = ConfigEntry(entry_id="test_opt", data={CONF_NAME: "Options Extractor"})
        opt_flow = BleIrkExtractorOptionsFlow(entry)
        manager = opt_flow._get_pairing_manager()

        with patch.object(manager, "async_get_adapters", new=AsyncMock(return_value=[{"address": "hci0", "name": "hci0"}])),              patch.object(manager, "async_enable_pairing_mode", new=AsyncMock()) as mock_enable,              patch.object(manager, "async_disable_pairing_mode", new=AsyncMock()) as mock_disable:

            res_extract = await opt_flow.async_step_extract_irk()
            self.assertEqual(res_extract["step_id"], "extract_irk")
            mock_enable.assert_called_once_with("hci0", timeout=DEFAULT_PAIRING_TIMEOUT)

            res_confirm = await opt_flow.async_step_extract_irk({"confirm_paired": True})
            self.assertEqual(res_confirm["step_id"], "extract_sync")
            mock_disable.assert_called_with("hci0")

            res_finish = await opt_flow.async_step_extract_sync({})
            self.assertEqual(res_finish["type"], "create_entry")
            self.assertEqual(res_finish["data"], {})

    async def test_options_flow_abort_disables_pairing(self) -> None:
        entry = ConfigEntry(entry_id="test_opt", data={CONF_NAME: "Options Extractor"})
        opt_flow = BleIrkExtractorOptionsFlow(entry)
        manager = opt_flow._get_pairing_manager()

        manager.set_adapter_pairing_mode("hci0", enabled=True, timeout=180)
        opt_flow._pairing_adapter = "hci0"

        res_abort = opt_flow.async_abort(reason="user_canceled")
        self.assertEqual(res_abort["type"], "abort")
        self.assertFalse(manager.adapter_properties["hci0"]["Discoverable"])
        self.assertFalse(manager.adapter_properties["hci0"]["Pairable"])

    async def test_options_flow_paths_update(self) -> None:
        entry = ConfigEntry(entry_id="test_opt", data={CONF_NAME: "Old", CONF_COPIED_PATH: "/config/bluez_irks"})
        opt_flow = BleIrkExtractorOptionsFlow(entry)

        with patch("custom_components.ble_irk_extractor.config_flow.scan_paths") as mock_scan:
            mock_scan.return_value = ScanResult(records=(), scanned_paths=("/config/new_path",), missing_paths=(), errors=(), scanned_at=None)
            res = await opt_flow.async_step_paths({
                CONF_NAME: "Updated Name",
                CONF_COPIED_PATH: "/config/new_path",
                CONF_HOST_PATH: "",
                CONF_SCAN_INTERVAL: 45,
            })
            self.assertEqual(res["type"], "create_entry")
            self.assertEqual(res["data"], {})
            self.assertEqual(entry.data[CONF_NAME], "Updated Name")
            self.assertEqual(entry.data[CONF_COPIED_PATH], "/config/new_path")


if __name__ == "__main__":
    unittest.main()
