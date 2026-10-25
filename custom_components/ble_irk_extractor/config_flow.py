"""Config flow for the BLE IRK Extractor integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
try:
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:
    ConfigFlowResult = dict[str, Any]
from homeassistant.core import callback
from homeassistant.helpers import selector

from .bluez import IrkRecord, format_irk_label, scan_paths
from .const import (
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
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .pairing import (
    BleIrkPairingManager,
    format_adapter_label,
    format_device_label,
)

_LOGGER = logging.getLogger(__name__)

_ACTION_OPTIONS = {
    "select_irk": "Select IRK Key",
    "add_irk": "Add IRK Key",
    "extract_irk": "Extract IRK Key",
}

_OPTIONS_ACTION_OPTIONS = {
    "select_irk": "View / Select Extracted IRK",
    "add_irk": "Add IRK Key",
    "extract_irk": "Extract IRK Key",
    "paths": "Configure BlueZ Paths",
}


def _clean_data(data: dict[str, Any]) -> dict[str, Any]:
    """Strip whitespace from configured paths and normalize types."""
    cleaned = dict(data)
    for key in (CONF_NAME, CONF_COPIED_PATH, CONF_HOST_PATH):
        if key in cleaned and isinstance(cleaned[key], str):
            cleaned[key] = cleaned[key].strip()
    return cleaned


def _paths(data: dict[str, Any]) -> list[str]:
    """Return the candidate paths configured in the flow."""
    return [
        str(data[key]).strip()
        for key in (CONF_COPIED_PATH, CONF_HOST_PATH)
        if data.get(key) and str(data[key]).strip()
    ]


def _paths_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the paths and scan interval schema."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_NAME,
                default=defaults.get(CONF_NAME, DEFAULT_NAME),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_COPIED_PATH,
                default=defaults.get(CONF_COPIED_PATH, DEFAULT_COPIED_PATH),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_HOST_PATH,
                default=defaults.get(CONF_HOST_PATH, DEFAULT_HOST_PATH),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="minutes",
                )
            ),
        }
    )


class BleIrkFlowHandlerMixin:
    """Shared steps and helpers for ConfigFlow and OptionsFlow."""

    hass: Any
    context: dict[str, Any]
    _pending_data: dict[str, Any] | None
    _pairing_adapter: str | None
    _pairing_manager: BleIrkPairingManager | None
    _discovered_devices: list[dict[str, Any]]
    _last_irk_record: IrkRecord | None
    _scanned_irk_records: list[IrkRecord]
    _selected_record: IrkRecord | None

    def _get_config_entry(self) -> config_entries.ConfigEntry | None:
        """Return the active config entry for options or reconfigure flows."""
        if hasattr(self, "config_entry") and self.config_entry is not None:
            return self.config_entry
        entry_id = self.context.get("entry_id")
        if entry_id:
            return self.hass.config_entries.async_get_entry(entry_id)
        return None

    def _get_pairing_manager(self) -> BleIrkPairingManager:
        """Return the pairing manager instance."""
        if self._pairing_manager is None:
            self._pairing_manager = BleIrkPairingManager(self.hass)
        return self._pairing_manager

    def _get_scan_paths(self) -> list[str]:
        """Return candidate paths to scan for BlueZ IRKs."""
        entry = self._get_config_entry()
        if entry is not None:
            paths = _paths(entry.data)
            if paths:
                return paths
        return [DEFAULT_COPIED_PATH, DEFAULT_HOST_PATH]

    def _abort_pairing_if_active(self) -> None:
        """Ensure adapter pairing mode is disabled."""
        if self._pairing_manager is not None:
            self._pairing_manager.disable_pairing_mode(self._pairing_adapter)

    async def _async_finish_flow(
        self,
        *,
        title: str = DEFAULT_NAME,
        data: dict[str, Any] | None = None,
        reason: str = "setup_completed",
    ) -> ConfigFlowResult:
        """Conclude flow appropriately for either ConfigFlow or OptionsFlow."""
        if isinstance(self, config_entries.OptionsFlow):
            return self.async_create_entry(title="", data={})

        entry = self._get_config_entry()
        if entry is None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            entry_data = data or _clean_data({
                CONF_NAME: DEFAULT_NAME,
                CONF_COPIED_PATH: DEFAULT_COPIED_PATH,
                CONF_HOST_PATH: DEFAULT_HOST_PATH,
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            })
            return self.async_create_entry(title=title, data=entry_data)

        return self.async_abort(reason=reason)

    # ------------------------------------------------------------------
    # Path 1: Select IRK Key -> Key Details Card
    # ------------------------------------------------------------------

    async def async_step_select_irk(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select an IRK from scanned BlueZ records."""
        paths = self._get_scan_paths()
        result = await self.hass.async_add_executor_job(scan_paths, paths)

        if not result.records:
            if user_input is not None and user_input.get("retry"):
                return await self.async_step_select_irk()
            return self.async_show_form(
                step_id="select_irk",
                data_schema=vol.Schema(
                    {
                        vol.Optional("retry", default=True): bool,
                    }
                ),
                errors={"base": "no_irks_found"},
                description_placeholders={
                    "copy_command": COMMAND_COPY_BLUEZ,
                    "paths": ", ".join(paths),
                },
            )

        # Sort by mtime descending (most recent first)
        records_by_mtime = sorted(
            result.records, key=lambda r: getattr(r, "mtime", 0.0), reverse=True
        )
        self._scanned_irk_records = records_by_mtime

        device_options = {
            r.irk: format_irk_label(r) for r in records_by_mtime
        }

        if user_input is not None:
            selected = user_input.get("selected_device") or user_input.get("selected_irk")
            chosen = next(
                (
                    r
                    for r in records_by_mtime
                    if r.irk == selected or r.address == selected
                ),
                None,
            )
            if chosen is not None:
                self._selected_record = chosen
                return await self.async_step_irk_details()

        return self.async_show_form(
            step_id="select_irk",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "selected_device", default=records_by_mtime[0].irk
                    ): vol.In(device_options),
                }
            ),
            description_placeholders={
                "device_count": str(len(records_by_mtime)),
            },
        )

    async def async_step_irk_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Display extracted IRK details and optionally forward to private_ble_device."""
        chosen = self._selected_record
        if chosen is None:
            return await self.async_step_select_irk()

        if user_input is not None:
            forward = user_input.get("forward_to_pbd", True)
            if forward:
                try:
                    await self.hass.config_entries.flow.async_init(
                        "private_ble_device",
                        context={"source": config_entries.SOURCE_USER},
                        data={
                            "address": chosen.address or "",
                            "irk": chosen.irk,
                            "name": chosen.display_name,
                        },
                    )
                except Exception:
                    _LOGGER.exception("Failed to initiate private_ble_device flow")

            return await self._async_finish_flow(
                reason="pbd_flow_started" if forward else "setup_completed"
            )

        return self.async_show_form(
            step_id="irk_details",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "irk_display", default=chosen.irk
                    ): selector.TextSelector(),
                    vol.Required(
                        "forward_to_pbd", default=True
                    ): selector.BooleanSelector(),
                }
            ),
            description_placeholders={
                "name": chosen.display_name,
                "address": chosen.address or "Unknown",
                "irk": chosen.irk,
            },
        )

    # ------------------------------------------------------------------
    # Path 2: Add IRK Key (Manual input)
    # ------------------------------------------------------------------

    async def async_step_add_irk(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manually supply an IRK and provision a Private BLE Device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = str(user_input.get("name") or "").strip()
            raw_irk = str(user_input.get("irk") or "")
            clean_irk = re.sub(r"[\s:\-]", "", raw_irk).upper()

            if (
                len(clean_irk) != 32
                or not re.fullmatch(r"^[0-9A-F]{32}$", clean_irk)
                or set(clean_irk) == {"0"}
            ):
                errors["irk"] = "invalid_irk_format"
                errors["base"] = "invalid_irk_format"
            else:
                device_name = name or "Personal BLE Device"
                try:
                    await self.hass.config_entries.flow.async_init(
                        "private_ble_device",
                        context={"source": config_entries.SOURCE_USER},
                        data={
                            "address": "",
                            "irk": clean_irk,
                            "name": device_name,
                        },
                    )
                except Exception:
                    _LOGGER.exception("Failed to initiate private_ble_device flow")

                return await self._async_finish_flow(reason="pbd_flow_started")

        return self.async_show_form(
            step_id="add_irk",
            data_schema=vol.Schema(
                {
                    vol.Optional("name", default="Personal BLE Device"): selector.TextSelector(),
                    vol.Required("irk"): selector.TextSelector(),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Path 3: Extract IRK Key (Pairing Mode + Terminal Sync Card)
    # ------------------------------------------------------------------

    async def async_step_extract_irk(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enable pairing mode on the adapter and wait for phone inbound pairing."""
        manager = self._get_pairing_manager()

        if self._pairing_adapter is None:
            adapters = await manager.async_get_adapters()
            if adapters:
                self._pairing_adapter = adapters[0]["address"]
            else:
                self._pairing_adapter = "default"

        if user_input is not None:
            await manager.async_disable_pairing_mode(self._pairing_adapter)
            return await self.async_step_extract_sync()

        await manager.async_enable_pairing_mode(
            self._pairing_adapter, timeout=DEFAULT_PAIRING_TIMEOUT
        )

        return self.async_show_form(
            step_id="extract_irk",
            data_schema=vol.Schema(
                {
                    vol.Required("confirm_paired", default=True): bool,
                }
            ),
            description_placeholders={
                "timeout": str(DEFAULT_PAIRING_TIMEOUT),
                "adapter": str(self._pairing_adapter or "default"),
            },
        )

    async def async_step_extract_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Display the terminal sync command card; does NOT trigger private_ble_device."""
        manager = self._get_pairing_manager()
        await manager.async_disable_pairing_mode(self._pairing_adapter)

        if user_input is not None:
            return await self._async_finish_flow(reason="setup_completed")

        return self.async_show_form(
            step_id="extract_sync",
            data_schema=vol.Schema({}),
            description_placeholders={
                "copy_command": COMMAND_COPY_BLUEZ,
            },
        )

    # ------------------------------------------------------------------
    # Path Configuration & Overrides
    # ------------------------------------------------------------------

    async def async_step_paths(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure directory paths to scan for BlueZ IRKs."""
        errors: dict[str, str] = {}
        entry = self._get_config_entry()

        if user_input is not None:
            data = _clean_data(user_input)
            paths = _paths(data)

            if not paths:
                errors["base"] = "no_paths"
            else:
                result = await self.hass.async_add_executor_job(
                    scan_paths, paths
                )
                if not result.scanned_paths and not user_input.get(
                    CONF_SETUP_ANYWAY, False
                ):
                    errors["base"] = "no_valid_paths"
                    self._pending_data = data
                    return self.async_show_form(
                        step_id="paths",
                        data_schema=self._schema_with_override(data),
                        errors=errors,
                        description_placeholders={
                            "copied_path": data.get(CONF_COPIED_PATH, ""),
                            "host_path": data.get(CONF_HOST_PATH, ""),
                        },
                    )

                if isinstance(self, config_entries.OptionsFlow):
                    assert hasattr(self, "config_entry") and self.config_entry is not None
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={**self.config_entry.data, **data},
                    )
                    return self.async_create_entry(title="", data={})

                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()

                title = data.get(CONF_NAME) or DEFAULT_NAME
                return self.async_create_entry(title=title, data=data)

        defaults = self._pending_data or (
            dict(entry.data) if entry else {
                CONF_NAME: DEFAULT_NAME,
                CONF_COPIED_PATH: DEFAULT_COPIED_PATH,
                CONF_HOST_PATH: DEFAULT_HOST_PATH,
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            }
        )
        return self.async_show_form(
            step_id="paths",
            data_schema=_paths_schema(defaults),
            errors=errors,
            description_placeholders={
                "copied_path": DEFAULT_COPIED_PATH,
                "host_path": DEFAULT_HOST_PATH,
            },
        )

    def _schema_with_override(
        self, defaults: dict[str, Any]
    ) -> vol.Schema:
        """Return the paths schema with the override checkbox included."""
        schema = _paths_schema(defaults).schema
        schema[vol.Optional(CONF_SETUP_ANYWAY, default=False)] = (
            selector.BooleanSelector()
        )
        return vol.Schema(schema)


class BleIrkExtractorConfigFlow(
    BleIrkFlowHandlerMixin, config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for BLE IRK Extractor."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._pending_data = None
        self._pairing_adapter = None
        self._pairing_manager = None
        self._discovered_devices = []
        self._last_irk_record = None
        self._scanned_irk_records = []
        self._selected_record = None

    def async_abort(self, *, reason: str, **kwargs: Any) -> ConfigFlowResult:
        """Ensure adapter pairing mode is reverted if the flow is aborted."""
        self._abort_pairing_if_active()
        return super().async_abort(reason=reason, **kwargs)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Main setup entry step offering 3 primary paths."""
        if user_input is not None:
            action = user_input.get("action")
            if action == "select_irk":
                return await self.async_step_select_irk()
            if action == "add_irk":
                return await self.async_step_add_irk()
            if action == "extract_irk":
                return await self.async_step_extract_irk()
            # Legacy actions
            if action in ("configure_paths", "paths"):
                return await self.async_step_paths()
            if action == "pair_device":
                return await self.async_step_pair_device()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="select_irk"): vol.In(
                        _ACTION_OPTIONS
                    ),
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of paths/interval via the reconfigure step."""
        entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        assert entry is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            data = _clean_data(user_input)
            paths = _paths(data)

            if not paths:
                errors["base"] = "no_paths"
            else:
                result = await self.hass.async_add_executor_job(
                    scan_paths, paths
                )
                if not result.scanned_paths and not user_input.get(
                    CONF_SETUP_ANYWAY, False
                ):
                    errors["base"] = "no_valid_paths"
                    return self.async_show_form(
                        step_id="reconfigure",
                        data_schema=self._schema_with_override(data),
                        errors=errors,
                        description_placeholders={
                            "copied_path": data.get(CONF_COPIED_PATH, ""),
                            "host_path": data.get(CONF_HOST_PATH, ""),
                        },
                    )

                return self.async_update_reload_and_abort(
                    entry,
                    data=data,
                    reason="reconfigure_successful",
                )

        defaults = dict(entry.data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_paths_schema(defaults),
            errors=errors,
            description_placeholders={
                "copied_path": defaults.get(
                    CONF_COPIED_PATH, DEFAULT_COPIED_PATH
                ),
                "host_path": defaults.get(CONF_HOST_PATH, DEFAULT_HOST_PATH),
            },
        )

    # ------------------------------------------------------------------
    # Legacy Active Pairing Flow Steps
    # ------------------------------------------------------------------

    async def async_step_pair_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Discover and pair with a Bluetooth device to extract its IRK."""
        errors: dict[str, str] = {}
        manager = self._get_pairing_manager()

        if user_input is not None:
            self._pairing_adapter = user_input.get("adapter")
            return await self.async_step_discover_devices()

        adapters = await manager.async_get_adapters()
        if not adapters:
            return self.async_abort(reason="no_adapters")

        if len(adapters) == 1:
            self._pairing_adapter = adapters[0]["address"]
            return await self.async_step_discover_devices()

        adapter_options = {a["address"]: a["name"] for a in adapters}
        return self.async_show_form(
            step_id="pair_device",
            data_schema=vol.Schema(
                {
                    vol.Required("adapter"): vol.In(adapter_options),
                }
            ),
            errors=errors,
        )

    async def async_step_discover_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Scan for devices on the selected adapter."""
        errors: dict[str, str] = {}
        manager = self._get_pairing_manager()

        if user_input is not None:
            device_address = user_input["device"]
            return await self.async_step_pair_confirm({"device": device_address})

        adapter = self._pairing_adapter or "default"
        self._discovered_devices = await manager.async_discover_devices(adapter)

        if not self._discovered_devices:
            return self.async_show_form(
                step_id="discover_devices",
                data_schema=vol.Schema(
                    {
                        vol.Optional("retry", default=True): bool,
                    }
                ),
                errors={"base": "no_devices_found"},
            )

        device_options = {
            d["address"]: d["label"] for d in self._discovered_devices
        }
        return self.async_show_form(
            step_id="discover_devices",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(device_options),
                }
            ),
            errors=errors,
        )

    async def async_step_pair_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm and execute pairing with the chosen device."""
        device_address = (user_input or {}).get("device", "")
        manager = self._get_pairing_manager()
        paths = self._get_scan_paths()

        pair_success = await manager.async_pair_device(device_address)
        if not pair_success:
            _LOGGER.info(
                "In-flow pairing not supported; falling back to manual bond check"
            )

        irk_record = await manager.async_wait_for_bond_record(
            device_address, paths
        )
        if irk_record is None:
            irk_record = await manager.async_extract_irk_for_device(
                device_address, paths
            )

        if irk_record is None:
            return self.async_show_form(
                step_id="pairing_manual_guidance",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "device": device_address,
                    "copy_command": COMMAND_COPY_BLUEZ,
                },
            )

        self._last_irk_record = irk_record
        return await self.async_step_pairing_result(
            {"irk_record": irk_record}
        )

    async def async_step_pairing_manual_guidance(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show manual pairing guidance when in-flow pairing could not extract IRK."""
        if user_input is not None:
            return await self.async_step_user()
        return self.async_show_form(
            step_id="pairing_manual_guidance",
            data_schema=vol.Schema({}),
            description_placeholders={
                "device": "device",
                "copy_command": COMMAND_COPY_BLUEZ,
            },
        )

    async def async_step_pairing_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Display the extracted IRK and hand off to private_ble_device."""
        irk_record = (user_input or {}).get(
            "irk_record", self._last_irk_record
        )
        if irk_record is None:
            return await self.async_step_user()

        if user_input is not None and "irk_record" not in user_input:
            create_pbd = user_input.get("create_private_ble_device", True)
            entry = self._get_config_entry()
            if entry is None:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                default_paths = [DEFAULT_COPIED_PATH, DEFAULT_HOST_PATH]
                entry_data = _clean_data({
                    CONF_NAME: DEFAULT_NAME,
                    CONF_COPIED_PATH: default_paths[0],
                    CONF_HOST_PATH: default_paths[1],
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                })
                if create_pbd:
                    try:
                        await self.hass.config_entries.flow.async_init(
                            "private_ble_device",
                            context={"source": config_entries.SOURCE_USER},
                            data={
                                "address": irk_record.address,
                                "irk": irk_record.irk,
                                "name": irk_record.display_name,
                            },
                        )
                    except Exception:
                        _LOGGER.exception(
                            "Failed to initiate private_ble_device flow"
                        )
                return self.async_create_entry(
                    title=DEFAULT_NAME, data=entry_data
                )

            if create_pbd:
                try:
                    await self.hass.config_entries.flow.async_init(
                        "private_ble_device",
                        context={"source": config_entries.SOURCE_USER},
                        data={
                            "address": irk_record.address,
                            "irk": irk_record.irk,
                            "name": irk_record.display_name,
                        },
                    )
                except Exception:
                    _LOGGER.exception(
                        "Failed to initiate private_ble_device flow"
                    )
            return self.async_abort(reason="pbd_flow_started")

        return self.async_show_form(
            step_id="pairing_result",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "create_private_ble_device", default=True
                    ): bool,
                }
            ),
            description_placeholders={
                "name": irk_record.display_name,
                "address": irk_record.address or "Unknown",
                "irk": irk_record.irk,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return BleIrkExtractorOptionsFlow(config_entry)


class BleIrkExtractorOptionsFlow(
    BleIrkFlowHandlerMixin, config_entries.OptionsFlow
):
    """Handle options for BLE IRK Extractor."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        super().__init__(config_entry)
        self.config_entry = config_entry
        self._pending_data = None
        self._pairing_adapter = None
        self._pairing_manager = None
        self._discovered_devices = []
        self._last_irk_record = None
        self._scanned_irk_records = []
        self._selected_record = None

    def async_abort(self, *, reason: str, **kwargs: Any) -> ConfigFlowResult:
        """Ensure adapter pairing mode is reverted if the flow is aborted."""
        self._abort_pairing_if_active()
        return super().async_abort(reason=reason, **kwargs)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options menu or direct path reconfiguration."""
        if user_input is not None:
            action = user_input.get("action")
            if action == "select_irk":
                return await self.async_step_select_irk()
            if action == "add_irk":
                return await self.async_step_add_irk()
            if action == "extract_irk":
                return await self.async_step_extract_irk()
            if action in ("paths", "configure_paths"):
                return await self.async_step_paths()

            # Direct paths submission backward compatibility
            if CONF_COPIED_PATH in user_input or CONF_HOST_PATH in user_input:
                return await self.async_step_paths(user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="select_irk"): vol.In(
                        _OPTIONS_ACTION_OPTIONS
                    ),
                }
            ),
        )
