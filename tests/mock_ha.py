"""Mock Home Assistant modules for unit testing."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

def setup_ha_mocks():
    """Register mock modules in sys.modules if not already present."""
    if "homeassistant" in sys.modules:
        return

    # Mock voluptuous
    vol = types.ModuleType("voluptuous")
    class Schema:
        def __init__(self, schema=None):
            self.schema = schema or {}
        def __call__(self, val):
            return val
    class InValidator:
        def __init__(self, container):
            self.container = container
        def __call__(self, val):
            if self.container and val not in self.container:
                raise ValueError(f"Value '{val}' not in {self.container}")
            return val

    def In(container):
        return InValidator(container)
    def Required(key, default=None):
        return key
    def Optional(key, default=None):
        return key

    vol.Schema = Schema
    vol.In = In
    vol.Required = Required
    vol.Optional = Optional
    sys.modules["voluptuous"] = vol

    # Mock bluetooth_adapters library
    bt_adapters = types.ModuleType("bluetooth_adapters")

    class BluetoothAdaptersMock:
        def __init__(self, adapters=None):
            if adapters is not None:
                self.adapters = adapters
            else:
                self.adapters = {
                    "hci0": {
                        "address": "00:11:22:33:44:55",
                        "name": "hci0",
                        "passive_scan": True,
                        "manufacturer": "Linux Foundation",
                        "product": "Bluetooth Adapter",
                    }
                }

        async def refresh(self):
            pass

    def get_adapters():
        return BluetoothAdaptersMock()

    bt_adapters.get_adapters = get_adapters
    bt_adapters.BluetoothAdapters = BluetoothAdaptersMock
    bt_adapters.ADAPTER_ADDRESS = "address"
    bt_adapters.ADAPTER_PASSIVE_SCAN = "passive_scan"
    sys.modules["bluetooth_adapters"] = bt_adapters

    # Mock homeassistant core
    ha = types.ModuleType("homeassistant")
    ha_core = types.ModuleType("homeassistant.core")
    def callback(func):
        return func
    ha_core.callback = callback
    class HomeAssistant:
        def __init__(self):
            self.data = {}
            self.config_entries = MagicMock()
            self.config_entries.async_get_entry.return_value = None
            def _update_entry(entry, title=None, data=None, options=None):
                if title:
                    entry.title = title
                if data is not None:
                    entry.data = data
                if options is not None:
                    entry.options = options
            self.config_entries.async_update_entry.side_effect = _update_entry
        async def async_add_executor_job(self, func, *args, **kwargs):
            return func(*args, **kwargs)
    ha_core.HomeAssistant = HomeAssistant

    ha_const = types.ModuleType("homeassistant.const")
    class Platform:
        SENSOR = "sensor"
        BUTTON = "button"
    class EntityCategory:
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"
    ha_const.Platform = Platform
    ha_const.EntityCategory = EntityCategory

    # Mock config_entries
    ha_ce = types.ModuleType("homeassistant.config_entries")
    ha_ce.SOURCE_USER = "user"
    ha_ce.ConfigFlowResult = dict
    class ConfigEntry:
        def __init__(self, entry_id="test_entry_id", title="BLE IRK Extractor", data=None, options=None):
            self.entry_id = entry_id
            self.title = title
            self.data = data or {}
            self.options = options or {}
            self._unloads = []
        def async_on_unload(self, func):
            self._unloads.append(func)
        def add_update_listener(self, listener):
            return listener
    ha_ce.ConfigEntry = ConfigEntry

    class ConfigFlow:
        def __init_subclass__(cls, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)
            cls._domain = domain

        def __init__(self):
            self.hass = HomeAssistant()
            self.context = {}
            self.unique_id = None

        async def async_set_unique_id(self, unique_id=None):
            self.unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            pass

        def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
                "description_placeholders": description_placeholders or {},
            }

        def async_create_entry(self, *, title, data):
            return {
                "type": "create_entry",
                "title": title,
                "data": data,
            }

        def async_abort(self, *, reason):
            return {
                "type": "abort",
                "reason": reason,
            }

        def async_update_reload_and_abort(self, entry, data=None, reason=None):
            if data:
                entry.data.update(data)
            return {
                "type": "abort",
                "reason": reason or "reconfigure_successful",
            }
    ha_ce.ConfigFlow = ConfigFlow

    class OptionsFlow:
        def __init__(self, config_entry=None):
            self.config_entry = config_entry
            self.hass = HomeAssistant()

        def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
                "description_placeholders": description_placeholders or {},
            }

        def async_create_entry(self, *, title, data):
            return {
                "type": "create_entry",
                "title": title,
                "data": data,
            }

        def async_abort(self, *, reason):
            return {
                "type": "abort",
                "reason": reason,
            }
    ha_ce.OptionsFlow = OptionsFlow

    # Mock helpers
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_helpers_sel = types.ModuleType("homeassistant.helpers.selector")
    class TextSelector:
        def __init__(self, config=None):
            self.config = config
    class NumberSelector:
        def __init__(self, config=None):
            self.config = config
    class NumberSelectorConfig:
        def __init__(self, min=0, max=100, mode=None, unit_of_measurement=None):
            pass
    class NumberSelectorMode:
        BOX = "box"
    class BooleanSelector:
        def __init__(self, config=None):
            pass

    ha_helpers_sel.TextSelector = TextSelector
    ha_helpers_sel.NumberSelector = NumberSelector
    ha_helpers_sel.NumberSelectorConfig = NumberSelectorConfig
    ha_helpers_sel.NumberSelectorMode = NumberSelectorMode
    ha_helpers_sel.BooleanSelector = BooleanSelector

    ha_helpers_uc = types.ModuleType("homeassistant.helpers.update_coordinator")
    class UpdateFailed(Exception):
        pass
    class DataUpdateCoordinator:
        def __init__(self, hass, logger, name, update_interval=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None
        async def async_refresh(self):
            self.data = await self._async_update_data()
        async def async_config_entry_first_refresh(self):
            self.data = await self._async_update_data()
        async def _async_update_data(self):
            raise NotImplementedError
        def __class_getitem__(cls, item):
            return cls

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator
        def __class_getitem__(cls, item):
            return cls

    ha_helpers_uc.UpdateFailed = UpdateFailed
    ha_helpers_uc.DataUpdateCoordinator = DataUpdateCoordinator
    ha_helpers_uc.CoordinatorEntity = CoordinatorEntity

    ha_helpers_dev = types.ModuleType("homeassistant.helpers.device_registry")
    class DeviceInfo(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
    ha_helpers_dev.DeviceInfo = DeviceInfo

    ha_helpers_ep = types.ModuleType("homeassistant.helpers.entity_platform")
    ha_helpers_ep.AddEntitiesCallback = MagicMock()

    # Mock components
    ha_comp = types.ModuleType("homeassistant.components")
    ha_comp_bt = types.ModuleType("homeassistant.components.bluetooth")
    class BluetoothScanningMode:
        ACTIVE = "active"
        PASSIVE = "passive"
    class BluetoothCallbackMatcher:
        def __init__(self, address=None):
            self.address = address
    class BluetoothServiceInfoBleak:
        def __init__(self, address, name=None, rssi=None, manufacturer_data=None):
            self.address = address
            self.name = name
            self.rssi = rssi
            self.manufacturer_data = manufacturer_data or {}
    ha_comp_bt.BluetoothScanningMode = BluetoothScanningMode
    ha_comp_bt.BluetoothCallbackMatcher = BluetoothCallbackMatcher
    ha_comp_bt.BluetoothServiceInfoBleak = BluetoothServiceInfoBleak
    ha_comp_bt.async_scanner_count = MagicMock(return_value=1)
    ha_comp_bt.async_get_scanner = MagicMock()
    ha_comp_bt.async_discovered_service_info = MagicMock(return_value=[])
    ha_comp_bt.async_register_callback = MagicMock(return_value=MagicMock())
    ha_comp_bt.async_pair_device = MagicMock()

    ha_comp_sensor = types.ModuleType("homeassistant.components.sensor")
    class SensorEntity:
        @property
        def unique_id(self):
            return getattr(self, "_attr_unique_id", None)
        @property
        def entity_category(self):
            return getattr(self, "_attr_entity_category", None)
        @property
        def translation_key(self):
            return getattr(self, "_attr_translation_key", None)
        @property
        def icon(self):
            return getattr(self, "_attr_icon", None)
    ha_comp_sensor.SensorEntity = SensorEntity

    ha_comp_button = types.ModuleType("homeassistant.components.button")
    class ButtonEntity:
        pass
    ha_comp_button.ButtonEntity = ButtonEntity

    ha_helpers.selector = ha_helpers_sel
    ha_helpers.update_coordinator = ha_helpers_uc
    ha_helpers.device_registry = ha_helpers_dev
    ha_helpers.entity_platform = ha_helpers_ep
    ha_comp.bluetooth = ha_comp_bt
    ha_comp.sensor = ha_comp_sensor
    ha_comp.button = ha_comp_button

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.core"] = ha_core
    sys.modules["homeassistant.const"] = ha_const
    sys.modules["homeassistant.config_entries"] = ha_ce
    sys.modules["homeassistant.helpers"] = ha_helpers
    sys.modules["homeassistant.helpers.selector"] = ha_helpers_sel
    sys.modules["homeassistant.helpers.update_coordinator"] = ha_helpers_uc
    sys.modules["homeassistant.helpers.device_registry"] = ha_helpers_dev
    sys.modules["homeassistant.helpers.entity_platform"] = ha_helpers_ep
    sys.modules["homeassistant.components"] = ha_comp
    sys.modules["homeassistant.components.bluetooth"] = ha_comp_bt
    sys.modules["homeassistant.components.sensor"] = ha_comp_sensor
    sys.modules["homeassistant.components.button"] = ha_comp_button
