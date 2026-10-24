"""Config and options flows for the BLE IRK Extractor integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

try:  # Home Assistant >= 2024.4
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # pragma: no cover - older cores
    ConfigFlowResult = Any  # type: ignore[misc, assignment]

from .bluez import scan_paths
from .const import (
    CONF_COPIED_PATH,
    CONF_HOST_PATH,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SETUP_ANYWAY,
    DEFAULT_COPIED_PATH,
    DEFAULT_HOST_PATH,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .pairing import BleIrkPairingManager, DISCOVERY_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

_PATH_HELP = (
    "The integration checks both folders. On Home Assistant OS, copy the "
    "host's BlueZ data into /config/bluez_irks first; the host path is only "
    "readable when the integration runs directly on the BlueZ machine."
)

_ACTION_OPTIONS = {
    "configure_paths": "Configure BlueZ source paths",
    "pair_device": "Pair a new Bluetooth device and extract its IRK",
}


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the setup/options form schema."""
    defaults = defaults or {}
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
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            ),
        }
    )


def _clean_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalise form input before storing it in a config entry."""
    try:
        interval = int(
            user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
    except (TypeError, ValueError):
        interval = DEFAULT_SCAN_INTERVAL
    interval = max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, interval))

    name = str(user_input.get(CONF_NAME) or DEFAULT_NAME).strip()
    return {
        CONF_NAME: name or DEFAULT_NAME,
        CONF_COPIED_PATH: str(user_input.get(CONF_COPIED_PATH) or "").strip(),
        CONF_HOST_PATH: str(user_input.get(CONF_HOST_PATH) or "").strip(),
        CONF_SCAN_INTERVAL: interval,
    }


def _paths(data: dict[str, Any]) -> list[str]:
    """Return non-empty, de-duplicated source paths from form data."""
    paths: list[str] = []
    for key in (CONF_COPIED_PATH, CONF_HOST_PATH):
        value = str(data.get(key, "") or "").strip()
        if value and value not in paths:
            paths.append(value)
    return paths


class BleIrkExtractorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup, pairing, and reconfiguration."""

    VERSION = 1
    _pending_data: dict[str, Any] | None = None
    _pairing_adapter: str | None = None
    _pairing_device: str | None = None
    _pairing_device_name: str | None = None
    _discovered_devices: list[dict[str, Any]] = []
    _last_irk_record: Any | None = None  # IrkRecord from bluez module

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Menu to either configure paths or pair a new device."""
        if user_input is not None:
            action = user_input.get("action")
            if action == "pair_device":
                return await self.async_step_pair_device()
            if action == "configure_paths":
                return await self.async_step_paths()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("action"): vol.In(_ACTION_OPTIONS),
                }
            ),
        )

    async def async_step_paths(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the BlueZ paths and scan interval."""
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
                if not result.scanned_paths:
                    errors["base"] = "path_not_found"
                else:
                    await self.async_set_unique_id(DOMAIN)
                    self._abort_if_unique_id_configured()
                    if not result.records:
                        self._pending_data = data
                        return await self.async_step_no_keys()
                    return self.async_create_entry(
                        title=data[CONF_NAME],
                        data=data,
                    )

        return self.async_show_form(
            step_id="paths",
            data_schema=_schema(user_input),
            errors=errors,
            description_placeholders={"path_help": _PATH_HELP},
        )

    async def async_step_no_keys(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user finish setup even if no keys are present yet."""
        if self._pending_data is None:
            return self.async_abort(reason="no_pending_setup")

        if user_input is not None:
            if user_input.get(CONF_SETUP_ANYWAY, True):
                return self.async_create_entry(
                    title=self._pending_data[CONF_NAME],
                    data=self._pending_data,
                )
            return await self.async_step_paths(self._pending_data)

        return self.async_show_form(
            step_id="no_keys",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SETUP_ANYWAY, default=True
                    ): selector.BooleanSelector()
                }
            ),
            description_placeholders={
                "paths": ", ".join(_paths(self._pending_data)),
            },
        )

    # ------------------------------------------------------------------
    # Bluetooth pairing flow – discover -> select -> confirm -> pair -> result
    # ------------------------------------------------------------------

    async def async_step_pair_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a Bluetooth adapter to pair on."""
        errors: dict[str, str] = {}

        if user_input is not None:
            adapter_addr = user_input.get("adapter_address")
            if not adapter_addr:
                errors["base"] = "adapter_required"
            else:
                self._pairing_adapter = adapter_addr
                return await self.async_step_device_selection()

        # Get available adapters
        manager = BleIrkPairingManager(self.hass)
        adapters = await manager.async_get_adapters()
        if not adapters:
            return self.async_abort(reason="no_adapters")

        return self.async_show_form(
            step_id="pair_device",
            data_schema=vol.Schema(
                {
                    vol.Required("adapter_address"): vol.In(
                        [a["address"] for a in adapters]
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"adapter_count": str(len(adapters))},
        )

    async def async_step_device_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Scan for devices and let the user pick one."""
        if user_input is not None:
            device_address = user_input.get("device_address")
            if device_address:
                info = next(
                    (
                        d
                        for d in self._discovered_devices
                        if d["address"] == device_address
                    ),
                    None,
                )
                self._pairing_device = device_address
                self._pairing_device_name = info["name"] if info else device_address
                return await self.async_step_pair_confirm()

        # First call: discover devices
        manager = BleIrkPairingManager(self.hass)
        devices = await manager.async_discover_devices(self._pairing_adapter or "")

        self._discovered_devices = [
            {
                "address": d["address"],
                "name": d["name"],
                "rssi": d.get("rssi"),
                "label": d.get("label", d["address"]),
            }
            for d in devices
        ]

        device_options = {
            d["address"]: d.get("label", d.get("name", d["address"]))
            for d in devices
        }

        if not devices:
            return self.async_show_form(
                step_id="device_selection",
                data_schema=vol.Schema(
                    {
                        vol.Optional("device_address", default=""): vol.In({}),
                    }
                ),
                errors={"base": "no_devices_found"},
                description_placeholders={"start_timeout": str(DISCOVERY_TIMEOUT_SECONDS)},
            )

        return self.async_show_form(
            step_id="device_selection",
            data_schema=vol.Schema(
                {
                    vol.Required("device_address"): vol.In(device_options),
                }
            ),
            description_placeholders={"start_timeout": str(DISCOVERY_TIMEOUT_SECONDS)},
        )

    async def async_step_pair_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm pairing with the selected device."""
        if user_input is not None and user_input.get("confirm"):
            return await self.async_step_pairing()

        return self.async_show_form(
            step_id="pair_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("confirm", default=True): bool,
                }
            ),
            description_placeholders={
                "device_address": str(self._pairing_device or ""),
                "device_name": str(self._pairing_device_name or self._pairing_device or ""),
            },
        )

    async def async_step_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Perform the pairing and wait for the BlueZ bond record with IRK."""
        manager = BleIrkPairingManager(self.hass)
        device_address = self._pairing_device or ""

        success = await manager.async_pair_device(device_address)
        if not success:
            # Show manual pairing instructions
            return self.async_show_form(
                step_id="pairing_failed",
                data_schema=vol.Schema({}),
                errors={"base": "pairing_failed"},
                description_placeholders={
                    "device_address": str(device_address),
                    "device_name": str(self._pairing_device_name or device_address),
                },
            )

        # Wait for BlueZ to write the bond info file with the IRK.
        entry = self.hass.config_entries.async_get_entry(
            self.context.get("entry_id", "")
        )
        paths = _paths(entry.data) if entry else ["/config/bluez_irks", "/var/lib/bluetooth"]
        irk_record = await manager.async_wait_for_bond_record(
            device_address, paths
        )

        self._last_irk_record = irk_record

        if irk_record is None:
            return self.async_show_form(
                step_id="pairing_result",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            "create_pbd", default=False
                        ): bool,
                    }
                ),
                description_placeholders={
                    "device_address": str(device_address),
                    "device_name": str(self._pairing_device_name or device_address),
                    "irk": "IRK not found - BlueZ may not have written it yet.",
                },
            )

        return self.async_show_form(
            step_id="pairing_result",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "create_pbd", default=True
                    ): bool,
                }
            ),
            description_placeholders={
                "device_address": str(device_address),
                "device_name": str(self._pairing_device_name or device_address),
                "irk": str(irk_record.irk),
            },
        )

    async def async_step_pairing_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the pairing result and optionally start Private BLE Device flow."""
        if self._last_irk_record is None:
            return self.async_abort(reason="no_pending_setup")

        irk_record = self._last_irk_record
        self._last_irk_record = None

        user_input = user_input or {}
        create_pbd = bool(user_input.get("create_pbd"))

        # Create config entry if we don't have one yet (initial setup via pairing)
        entry = self.hass.config_entries.async_get_entry(
            self.context.get("entry_id", "")
        )
        if entry is None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            default_paths = ["/config/bluez_irks", "/var/lib/bluetooth"]
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
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Failed to initiate private_ble_device flow")
            return self.async_create_entry(title=DEFAULT_NAME, data=entry_data)

        # Existing entry: update if needed, optionally start PBD flow
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
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to initiate private_ble_device flow")

        return self.async_abort(reason="pbd_flow_started" if create_pbd else "setup_completed")

    # ------------------------------------------------------------------
    # Reconfigure
    # ------------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update the source paths or scan interval for an existing entry."""
        entry = self.hass.config_entries.async_get_entry(
            self.context.get("entry_id", "")
        )
        if entry is None:
            return self.async_abort(reason="unknown_entry")

        errors: dict[str, str] = {}

        if user_input is not None:
            data = _clean_data(user_input)
            paths = _paths(data)

            if not paths:
                errors["base"] = "no_paths"
            else:
                result = await self.hass.async_add_executor_job(scan_paths, paths)
                if not result.scanned_paths:
                    errors["base"] = "path_not_found"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry, title=data[CONF_NAME]
                    )
                    return self.async_update_reload_and_abort(
                        entry,
                        data=data,
                        reason="reconfigure_successful",
                    )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or entry.data),
            errors=errors,
            description_placeholders={"path_help": _PATH_HELP},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return BleIrkExtractorOptionsFlow(entry)


class BleIrkExtractorOptionsFlow(config_entries.OptionsFlow):
    """Allow changing paths and refresh interval from the UI."""

    def __init__(
        self, entry: config_entries.ConfigEntry | None = None
    ) -> None:
        super().__init__()
        self._entry = entry
        if entry is not None:
            try:
                self.config_entry = entry
            except AttributeError:
                pass

    @property
    def _config_entry(self) -> config_entries.ConfigEntry | None:
        return getattr(self, "config_entry", None) or self._entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the options form."""
        errors: dict[str, str] = {}
        entry = self._config_entry
        if entry is None:
            return self.async_abort(reason="unknown_entry")

        if user_input is not None:
            data = _clean_data(user_input)
            paths = _paths(data)

            if not paths:
                errors["base"] = "no_paths"
            else:
                result = await self.hass.async_add_executor_job(
                    scan_paths, paths
                )
                if not result.scanned_paths:
                    errors["base"] = "path_not_found"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry, title=data[CONF_NAME], data=data
                    )
                    return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(
                user_input if user_input is not None else dict(entry.data)
            ),
            errors=errors,
            description_placeholders={"path_help": _PATH_HELP},
        )
