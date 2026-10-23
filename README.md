# BLE IRK Extractor

A Home Assistant custom integration that reads **Bluetooth Low Energy Identity Resolving Keys (IRKs)** from BlueZ bond data and exposes them for use with Home Assistant's [Private BLE Device](https://www.home-assistant.io/integrations/private_ble_device/) integration.

Modern phones and other BLE devices rotate their advertised addresses using Resolvable Private Addresses (RPAs). To track one reliably, Home Assistant needs the device's IRK. This integration makes those keys easy to find.

## Features

- UI setup through **Settings → Devices & Services**
- Reads both the live BlueZ path and a Home Assistant-accessible copy
- Recursive scanner supports the original BlueZ layout, flattened files, and `*.info` files
- Configurable rescan interval
- **Rescan BlueZ data** button for immediate refreshes
- One sensor with a friendly `keys` attribute plus per-device metadata
- Diagnostics that redact IRK values
- No external Python dependencies

## Requirements

- Home Assistant 2024.1 or newer
- BlueZ bond data for the devices you want to track
- The device must have been paired with the Bluetooth adapter whose BlueZ data you are reading

## Installation

### HACS

1. Add this repository to HACS as a custom repository, category **Integration**.
2. Install **BLE IRK Extractor**.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/ble_irk_extractor` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Getting the BlueZ data

### If Home Assistant can see the host's BlueZ directory

If Home Assistant is running directly on the machine that owns the Bluetooth adapter, leave the host path at `/var/lib/bluetooth`. The integration reads it directly.

### Home Assistant OS (recommended path)

The Home Assistant OS container cannot read `/var/lib/bluetooth` from the host. Copy the bond records into `/config/bluez_irks` instead.

1. Install the **Advanced SSH & Web Terminal** add-on.
2. Disable **Protection mode** in the add-on configuration.
3. Start the add-on and open its terminal.
4. Pair the device with the host Bluetooth adapter:

   ```bash
   bluetoothctl
   power on
   pairable on
   discoverable on
   scan on
   ```

   Put your phone/device into Bluetooth pairing mode and pair it when it appears. Note the device address. Then leave `bluetoothctl`.

5. Copy all BlueZ `info` files, preserving their directory names:

   ```bash
   mkdir -p /config/bluez_irks
   cd /var/lib/bluetooth
   find . -name info -exec cp --parents {} /config/bluez_irks/ \;
   ```

   This produces a layout such as:

   ```text
   /config/bluez_irks/00:11:22:33:44:55/AA:BB:CC:DD:EE:FF/info
   ```

6. In Home Assistant, set **Copied BlueZ folder** to `/config/bluez_irks`.

> **Note:** Copying flattened files is supported too, but preserving the directory structure keeps duplicate device addresses separate and makes troubleshooting easier.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **BLE IRK Extractor**.
3. Fill in:
   - **Name** — friendly name for the extractor.
   - **Copied BlueZ folder** — usually `/config/bluez_irks`.
   - **Host BlueZ folder** — usually `/var/lib/bluetooth`; leave blank if it is not available.
   - **Rescan interval** — how often to reread the files, in minutes.
4. If the paths exist but no keys are found yet, the flow offers **Set up anyway**. The sensor will start working once keys appear.
5. The integration creates:
   - `sensor.ble_irk_extractor_irk_keys` (or a name based on your entry)
   - `button.ble_irk_extractor_rescan_bluez_data`

## Sensor attributes

The sensor state is the number of IRKs found.

The `keys` attribute is a mapping of friendly device label to 32-character IRK:

```yaml
keys:
  My Phone: 0123456789ABCDEF0123456789ABCDEF
  Pixel 9: FEDCBA9876543210FEDCBA9876543210
```

The `devices` attribute adds troubleshooting metadata:

```yaml
devices:
  My Phone:
    address: AA:BB:CC:DD:EE:FF
    irk: 0123456789ABCDEF0123456789ABCDEF
    source: /config/bluez_irks
```

Additional attributes:

- `sources` — paths that were scanned
- `missing_paths` — configured paths that did not exist
- `errors` — unreadable files or other scan problems
- `last_scan` — timestamp of the last scan
- `scan_interval_minutes` — current refresh interval

## Using an IRK with Private BLE Device

1. Open the BLE IRK Extractor sensor and copy the 32-character IRK from its `keys` attribute.
2. Go to **Settings → Devices & Services → Add Integration → Private BLE Device**.
3. Paste the IRK and complete setup.
4. For best coverage, use one or more ESPHome Bluetooth proxies near the device.

## Options and reconfiguration

- Use the integration's **Configure** button to change paths or the scan interval.
- Use the **Rescan BlueZ data** button after pairing a new device or copying fresh files.
- The integration reloads automatically when options change.

## Troubleshooting

### No keys found

- Confirm the device is paired and a BlueZ `info` file exists under `/var/lib/bluetooth`.
- On Home Assistant OS, confirm the files were copied to `/config/bluez_irks`.
- Check that the copied files contain an `[IdentityResolvingKey]` section with a `Key` value.
- Use the sensor's `errors` attribute and the **Rescan BlueZ data** button.

### Path not found

The setup form rejects paths that do not exist. Create the folder first:

```bash
mkdir -p /config/bluez_irks
```

### Host path is not available

This is expected on Home Assistant OS. Use the copied folder instead, or leave the host path blank.

### Multiple devices have the same name

The sensor appends ` (2)`, ` (3)`, and so on. The `devices` attribute shows the address and source for each entry.

### A paired device has no IRK

Some devices do not store an IRK in BlueZ, or were paired without privacy support. The integration can only expose what BlueZ recorded.

## Security

IRKs are long-lived identity secrets. Anyone who has a device's IRK can track that device's rotating addresses.

- The sensor exposes IRKs in state attributes, as required by the original design.
- Home Assistant stores state attributes in its database and exposes them through the API to authenticated users.
- The integration's diagnostics deliberately redact IRKs.
- Treat the sensor and diagnostics carefully when sharing logs or screenshots.

## Development

Run the scanner tests with:

```bash
python -m unittest discover -s tests -v
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
