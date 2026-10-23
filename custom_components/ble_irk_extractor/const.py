"""Constants for the BLE IRK Extractor integration."""

from __future__ import annotations

DOMAIN = "ble_irk_extractor"
DEFAULT_NAME = "BLE IRK Extractor"

# BlueZ stores bond data under /var/lib/bluetooth on the host. Home Assistant
# OS containers cannot see that path, so the README explains how to copy it to
# /config/bluez_irks. Both are offered by default; whichever exists is scanned.
DEFAULT_COPIED_PATH = "/config/bluez_irks"
DEFAULT_HOST_PATH = "/var/lib/bluetooth"

DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 1440

CONF_NAME = "name"
CONF_COPIED_PATH = "copied_path"
CONF_HOST_PATH = "host_path"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SETUP_ANYWAY = "setup_anyway"

BLUEZ_INFO_FILENAME = "info"
BLUEZ_GENERAL_SECTION = "General"
BLUEZ_IRK_SECTION = "IdentityResolvingKey"
