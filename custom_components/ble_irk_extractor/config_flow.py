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

_LOGGER = logging.getLogger(__name__)

_PATH_HELP = (
    "The integration checks both folders. On Home Assistant OS, copy the "
    "host's BlueZ data into /config/bluez_irks first; the host path is only "
    "readable when the integration runs directly on the BlueZ machine."
)


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
        interval = int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
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
    """Handle initial setup and reconfiguration."""

    VERSION = 1
    _pending_data: dict[str, Any] | None = None

    async def async_step_user(
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
                result = await self.hass.async_add_executor_job(scan_paths, paths)
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
            step_id="user",
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
            return await self.async_step_user(self._pending_data)

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
                "paths": ", ".join(_paths(self._pending_data))
            },
        )

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

    def __init__(self, entry: config_entries.ConfigEntry | None = None) -> None:
        # Older Home Assistant versions pass the entry to the constructor;
        # newer versions set ``config_entry`` on the flow themselves.  Keeping
        # our own reference makes this work on both without fighting the base
        # class property.
        self._entry = entry
        if entry is not None:
            try:
                self.config_entry = entry
            except AttributeError:
                # Newer cores expose config_entry as a read-only property.
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
                result = await self.hass.async_add_executor_job(scan_paths, paths)
                if not result.scanned_paths:
                    errors["base"] = "path_not_found"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        title=data[CONF_NAME],
                        data=data,
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
