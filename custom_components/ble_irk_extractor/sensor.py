"""Sensor platform for the BLE IRK Extractor integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
try:
    from homeassistant.const import EntityCategory
except ImportError:
    EntityCategory = None
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
    """Set up the IRK sensor."""
    coordinator: BleIrkCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BleIrkSensor(entry, coordinator)])


class BleIrkSensor(CoordinatorEntity[BleIrkCoordinator], SensorEntity):
    """Expose discovered IRKs and their source device metadata."""

    _attr_has_entity_name = True
    _attr_translation_key = "irk_keys"
    _attr_icon = "mdi:bluetooth"
    _attr_should_poll = False
    if EntityCategory is not None and hasattr(EntityCategory, "DIAGNOSTIC"):
        _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, entry: ConfigEntry, coordinator: BleIrkCoordinator
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_irk_keys"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="BlueZ",
            model="BlueZ bond database",
        )

    @property
    def native_value(self) -> int | None:
        """Return the number of IRKs currently available."""
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.records)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return the IRKs and structured device metadata.

        Exposes:
        - `keys`: friendly device label -> 32-character IRK
        - `devices`: friendly device label -> dict with address, irk, source, friendly_name, mac_address
        - `extracted_devices`: list of device dicts (friendly_name, mac_address, irk, source)
        - `sources`, `missing_paths`, `errors`, `last_scan`, `scan_interval_minutes`
        """
        data = self.coordinator.data
        if data is None:
            return {}

        keys: dict[str, str] = {}
        devices: dict[str, dict[str, str | None]] = {}
        extracted_list: list[dict[str, str | None]] = []

        for record in data.records:
            label = _unique_label(record.display_name, keys)
            keys[label] = record.irk
            dev_data = {
                "friendly_name": record.display_name,
                "mac_address": record.address,
                "address": record.address,
                "irk": record.irk,
                "source": record.source,
            }
            devices[label] = dev_data
            extracted_list.append(dev_data)

        return {
            "keys": keys,
            "devices": devices,
            "extracted_devices": extracted_list,
            "sources": list(data.scanned_paths),
            "missing_paths": list(data.missing_paths),
            "errors": list(data.errors),
            "last_scan": data.scanned_at.isoformat() if data.scanned_at else None,
            "scan_interval_minutes": self.coordinator.scan_interval_minutes,
        }


def _unique_label(base: str, existing: dict[str, str]) -> str:
    """Return a unique device label, preserving duplicate device names."""
    if base not in existing:
        return base

    counter = 2
    while f"{base} ({counter})" in existing:
        counter += 1
    return f"{base} ({counter})"
