"""Data update coordinator for the BLE IRK Extractor integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bluez import ScanResult, scan_paths
from .const import (
    CONF_COPIED_PATH,
    CONF_HOST_PATH,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def paths_from_entry(entry: ConfigEntry) -> list[str]:
    """Return the configured, non-empty source paths."""
    paths: list[str] = []
    for key in (CONF_COPIED_PATH, CONF_HOST_PATH):
        value = str(entry.data.get(key, "") or "").strip()
        if value and value not in paths:
            paths.append(value)
    return paths


class BleIrkCoordinator(DataUpdateCoordinator[ScanResult]):
    """Scan BlueZ bond records on a fixed interval."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        try:
            interval = int(
                entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            )
        except (TypeError, ValueError):
            interval = DEFAULT_SCAN_INTERVAL

        self.scan_interval_minutes = max(
            MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, interval)
        )
        self.paths = paths_from_entry(entry)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=self.scan_interval_minutes),
        )

    async def _async_update_data(self) -> ScanResult:
        """Read the BlueZ records without blocking the event loop."""
        try:
            return await self.hass.async_add_executor_job(scan_paths, self.paths)
        except Exception as err:  # noqa: BLE001 - surface a useful coordinator error
            raise UpdateFailed(f"Unable to scan BlueZ bond data: {err}") from err
