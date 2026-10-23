"""Button platform for the BLE IRK Extractor integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BleIrkCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the rescan button."""
    coordinator: BleIrkCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BleIrkRescanButton(entry, coordinator)])


class BleIrkRescanButton(CoordinatorEntity[BleIrkCoordinator], ButtonEntity):
    """Button that triggers an immediate BlueZ rescan."""

    _attr_has_entity_name = True
    _attr_translation_key = "rescan"
    _attr_icon = "mdi:refresh"

    def __init__(
        self, entry: ConfigEntry, coordinator: BleIrkCoordinator
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_rescan"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="BlueZ",
            model="BlueZ bond database",
        )

    async def async_press(self) -> None:
        """Ask the coordinator to rescan now."""
        await self.coordinator.async_refresh()
