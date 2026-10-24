"""Bluetooth pairing support for the BLE IRK Extractor integration.

This module discovers Bluetooth devices, attempts to pair with them through
Home Assistant's Bluetooth subsystem, and then extracts the Identity
Resolving Key (IRK) from the BlueZ bond record that BlueZ creates after a
successful bond.

Pairing through BlueZ requires an authentication agent (PIN, passkey, or
just-works confirmation). Home Assistant's Bluetooth integration handles the
agent side when its public pairing API is available. When it is not, the
integration falls back to guiding the user to pair manually through the
operating system's Bluetooth settings.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.core import HomeAssistant

from .bluez import IrkRecord, scan_paths

_LOGGER = logging.getLogger(__name__)

# How long to scan for discoverable devices.
DISCOVERY_TIMEOUT = 10
DISCOVERY_TIMEOUT_SECONDS = DISCOVERY_TIMEOUT
# After a successful bond, BlueZ may take a moment to write the bond record.
BOND_WRITE_DELAY = 3
BOND_WRITE_RETRIES = 3

APPLE_MANUFACTURER_ID = 0x004c  # 76 decimal (Apple, Inc.)


def format_adapter_label(
    address: str,
    name: str | None = None,
    product: str | None = None,
    iface: str | None = None,
) -> str:
    """Format a human-readable label for a Bluetooth adapter.

    Formats:
    - If iface and product/name are distinct: "{iface} - {product} ({address})"
    - If iface and address: "{iface} ({address})"
    - If name/product provided: "{name} ({address})"
    - Fallback: "{address}"
    """
    clean_addr = address.strip() if address else ""
    parts: list[str] = []

    if iface and iface.strip():
        c_iface = iface.strip()
        if c_iface.lower() != clean_addr.lower():
            parts.append(c_iface)

    candidate_name = (
        product.strip()
        if (product and product.strip())
        else (name.strip() if (name and name.strip()) else "")
    )
    if (
        candidate_name
        and candidate_name.lower() != clean_addr.lower()
        and candidate_name not in parts
    ):
        parts.append(candidate_name)

    if parts:
        prefix = " - ".join(parts)
        if clean_addr and clean_addr.lower() not in ("default", ""):
            return f"{prefix} ({clean_addr})"
        return prefix

    return clean_addr or "default"



def format_device_label(
    address: str,
    name: str | None = None,
    rssi: int | None = None,
    manufacturer_data: dict[int, bytes] | None = None,
) -> str:
    """Format a human-readable label for a discovered Bluetooth device.

    Formats:
    - Named device: "{name} ({address}) [{rssi} dBm]"
    - Unnamed Apple device: "Apple Device ({address}) [{rssi} dBm]"
    - Unnamed fallback: "{address} [{rssi} dBm]"

    If rssi is None, the trailing " [{rssi} dBm]" suffix is omitted.
    """
    clean_name = name.strip() if (name and name.strip()) else ""
    is_named = bool(clean_name and clean_name.lower() != address.lower())

    if is_named:
        base = f"{clean_name} ({address})"
    else:
        mfg = manufacturer_data or {}
        is_apple = (APPLE_MANUFACTURER_ID in mfg) or (76 in mfg)
        if is_apple:
            base = f"Apple Device ({address})"
        else:
            base = address

    if rssi is not None:
        return f"{base} [{rssi} dBm]"
    return base



class BleIrkPairingManager:
    """Manage Bluetooth discovery, pairing, and IRK extraction."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}
        self._unsub: Any = None

    async def async_get_adapters(self) -> list[dict[str, str]]:
        """Return available Bluetooth adapters.

        First attempts to enumerate host adapters using the bluetooth_adapters
        package (used by Home Assistant Core). If not present or empty, falls
        back to querying Home Assistant's active scanner count.
        """
        adapters_list: list[dict[str, str]] = []

        try:
            from bluetooth_adapters import get_adapters

            adapters_manager = get_adapters()
            await adapters_manager.refresh()
            raw_adapters = getattr(adapters_manager, "adapters", {})
            for iface, details in raw_adapters.items():
                addr = details.get("address") or iface
                product = details.get("product")
                name = details.get("name")
                label = format_adapter_label(
                    address=addr,
                    name=name,
                    product=product,
                    iface=iface,
                )
                adapters_list.append({
                    "address": addr,
                    "name": label,
                })
        except ImportError:
            _LOGGER.debug("bluetooth_adapters package not installed; checking HA scanners")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to query bluetooth_adapters")

        if not adapters_list:
            # Fallback 1: check active scanners in Home Assistant core bluetooth
            scanner_count_func = getattr(bluetooth, "async_scanner_count", None)
            if scanner_count_func:
                try:
                    count = scanner_count_func(self.hass, connectable=True)
                    if count == 0:
                        count = scanner_count_func(self.hass, connectable=False)
                    if count > 0:
                        suffix = "s" if count > 1 else ""
                        adapters_list.append({
                            "address": "default",
                            "name": f"Home Assistant Bluetooth ({count} active scanner{suffix})",
                        })
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Failed to query bluetooth scanner count")

        if not adapters_list:
            # Fallback 2: legacy or custom method if present
            legacy_get = getattr(bluetooth, "async_get_adapters", None)
            if legacy_get:
                try:
                    legacy_adapters = await legacy_get(self.hass)
                except TypeError:
                    legacy_adapters = await self.hass.async_add_executor_job(
                        legacy_get, self.hass
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Failed legacy async_get_adapters")
                    legacy_adapters = []

                if legacy_adapters:
                    for adapter in legacy_adapters:
                        addr = getattr(adapter, "address", str(adapter))
                        name = getattr(adapter, "name", None)
                        label = format_adapter_label(address=addr, name=name)
                        adapters_list.append({"address": addr, "name": label})

        if not adapters_list:
            _LOGGER.warning(
                "No Bluetooth adapters or scanners detected. Ensure the Bluetooth "
                "integration is enabled in Home Assistant under Settings -> Devices & Services"
            )

        return adapters_list

    async def async_discover_devices(
        self, adapter_address: str
    ) -> list[dict[str, Any]]:
        """Scan for discoverable Bluetooth devices on an adapter."""
        self._discovered = {}

        def _callback(
            info: BluetoothServiceInfoBleak, change: Any = None
        ) -> None:
            self._discovered[info.address] = info

        # Pre-seed with any devices already discovered by Home Assistant
        discovered_info_func = getattr(bluetooth, "async_discovered_service_info", None)
        if discovered_info_func:
            try:
                for info in discovered_info_func(self.hass, connectable=False):
                    self._discovered[info.address] = info
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not retrieve async_discovered_service_info")

        # Verify scanner availability
        scanner = None
        get_scanner_func = getattr(bluetooth, "async_get_scanner", None)
        if get_scanner_func:
            try:
                scanner = get_scanner_func(self.hass)
            except TypeError:
                try:
                    scanner = get_scanner_func(self.hass, adapter_address)
                except Exception:  # noqa: BLE001
                    scanner = None
            except Exception:  # noqa: BLE001
                scanner = None

        if not scanner:
            scanner_count_func = getattr(bluetooth, "async_scanner_count", None)
            if scanner_count_func:
                try:
                    if scanner_count_func(self.hass, connectable=False) > 0:
                        scanner = True
                except Exception:  # noqa: BLE001
                    pass

        if not scanner and not self._discovered:
            _LOGGER.warning("No Bluetooth scanner available for discovery on %s", adapter_address)
            return []

        # Register listener for live advertisements
        register_cb = getattr(bluetooth, "async_register_callback", None)
        if register_cb:
            try:
                self._unsub = register_cb(
                    self.hass,
                    _callback,
                    BluetoothCallbackMatcher(address=None),
                    mode=BluetoothScanningMode.ACTIVE,
                )
            except TypeError:
                try:
                    self._unsub = register_cb(
                        self.hass,
                        _callback,
                        BluetoothCallbackMatcher(address=None),
                    )
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
        elif hasattr(bluetooth, "async_register_scanner"):
            try:
                self._unsub = bluetooth.async_register_scanner(
                    self.hass,
                    _callback,
                    BluetoothCallbackMatcher(address=None),
                    scanning_mode=BluetoothScanningMode.ACTIVE,
                )
            except Exception:  # noqa: BLE001
                pass

        try:
            await asyncio.sleep(DISCOVERY_TIMEOUT_SECONDS)
        finally:
            await self.async_stop_discovery()

        results: list[dict[str, Any]] = []
        for addr, info in sorted(
            self._discovered.items(),
            key=lambda item: item[1].rssi if item[1].rssi is not None else -999,
            reverse=True,
        ):
            mfg_data = getattr(info, "manufacturer_data", None) or {}
            clean_name = info.name.strip() if (info.name and info.name.strip()) else ""
            if clean_name and clean_name.lower() != addr.lower():
                dev_name = clean_name
            elif (APPLE_MANUFACTURER_ID in mfg_data) or (76 in mfg_data):
                dev_name = "Apple Device"
            else:
                dev_name = addr

            label = format_device_label(
                address=addr,
                name=info.name,
                rssi=info.rssi,
                manufacturer_data=mfg_data,
            )

            results.append(
                {
                    "address": addr,
                    "name": dev_name,
                    "rssi": info.rssi,
                    "label": label,
                    "manufacturer_data": mfg_data,
                }
            )

        return results

    async def async_stop_discovery(self) -> None:
        """Stop an active discovery scan."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def async_pair_device(self, device_address: str) -> bool:
        """Attempt to pair with a Bluetooth device.

        Returns True if pairing succeeded. Falls back to False when Home
        Assistant does not expose a public pairing API, in which case the
        caller should guide the user to pair manually.
        """
        pair_func = getattr(bluetooth, "async_pair_device", None)
        if not pair_func:
            _LOGGER.warning(
                "Home Assistant does not expose async_pair_device; "
                "pair %s manually through Bluetooth settings",
                device_address,
            )
            return False

        try:
            result = await pair_func(self.hass, device_address)
            return bool(result)
        except Exception:  # noqa: BLE001 - pairing can fail for many reasons
            _LOGGER.exception("Pairing failed for %s", device_address)
            return False

    def extract_irk_for_device(
        self, device_address: str, paths: list[str]
    ) -> IrkRecord | None:
        """Return the IRK record for a device from BlueZ bond data."""
        normalized_address = device_address.upper()
        result = scan_paths(paths)

        for record in result.records:
            if record.address and record.address.upper() == normalized_address:
                return record

        # Some BlueZ versions store the address differently; try a name match.
        for record in result.records:
            if record.name and record.name.lower() == device_address.lower():
                return record

        return None

    async def async_extract_irk_for_device(
        self, device_address: str, paths: list[str]
    ) -> IrkRecord | None:
        """Return the IRK record for a device from BlueZ bond data asynchronously."""
        return await self.hass.async_add_executor_job(
            self.extract_irk_for_device, device_address, paths
        )

    async def async_wait_for_bond_record(
        self,
        device_address: str,
        paths: list[str],
    ) -> IrkRecord | None:
        """Poll BlueZ bond data until the paired device's IRK appears."""
        for _attempt in range(BOND_WRITE_RETRIES):
            record = await self.hass.async_add_executor_job(
                self.extract_irk_for_device, device_address, paths
            )
            if record is not None:
                return record
            _LOGGER.debug(
                "Bond record not yet visible for %s, retrying in %ss",
                device_address,
                BOND_WRITE_DELAY,
            )
            await asyncio.sleep(BOND_WRITE_DELAY)
        return None
