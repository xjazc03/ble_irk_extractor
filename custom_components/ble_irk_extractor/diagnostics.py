"""Diagnostics support for the BLE IRK Extractor integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with IRK values redacted.

    IRKs are long-lived identity secrets.  Diagnostics are commonly pasted
    into public issue trackers, so only metadata is included here.
    """
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data = coordinator.data if coordinator is not None else None

    records: list[dict[str, Any]] = []
    if data is not None:
        for record in data.records:
            records.append(
                {
                    "name": record.name,
                    "address": record.address,
                    "source": record.source,
                    "irk": "**redacted**",
                }
            )

    return {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "scan": {
            "scanned_paths": list(data.scanned_paths) if data else [],
            "missing_paths": list(data.missing_paths) if data else [],
            "errors": list(data.errors) if data else [],
            "scanned_at": data.scanned_at.isoformat() if data else None,
            "record_count": len(records),
            "records": records,
        },
    }
