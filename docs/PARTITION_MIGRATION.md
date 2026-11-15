# OTA partition migration (old 4MB layout -> 16MB layout, no USB)

Runbook for Onju devices whose OTA fails with:

```
ERROR ... The OTA partition on the ESP is too small. ESPHome needs to resize this partition.
Please flash over USB or update the partition table over the air.
```

Done successfully on one device with ESPHome 2026.10.2.

## Background

- Devices first flashed via USB with an old/4MB-style config have `app0`/`app1` = `0x1C0000` (1.75MB) each.
  The duplex firmware with `compiler_optimization: PERF` is ~2.4MB (with `SIZE` still ~2.1MB) -> does not fit.
  Leaving out sounds is not enough (all embedded sounds together are only ~200kB).
- OTA only replaces the app, not the partition table. ESPHome >= 2026.10 can write the partition table and the
  bootloader via OTA, but only if the **running** firmware was built with `ota: allow_partition_access: true`.
- The old bootloader carries **4MB** as flash size in its header. The app and the bootloader validate the
  partition table against that size (`esp_partition_table_verify`, uses `g_rom_flashchip.chip_size`).
  A 16MB table with the old bootloader is rejected by ESPHome before writing (good) - and would make the
  bootloader refuse to boot if it were written anyway. So the **bootloader must be updated before the table**.
- The Device Builder UI (1.25) only offers the OTA bootloader update, not the partition table -> use the
  `esphome` CLI inside the Device Builder container.

## Prerequisites

- Device Builder runs in rootless Podman; `/config` in the container = this repo folder.
  Container name changes on every restart: `podman ps --format '{{.Names}} {{.Image}}'` (below: `<c>`).
- Stable power for the device during steps 2 and 3 (only short windows, but a power loss there needs USB recovery).
- Device settings stored in NVS are lost (volume, switch states, Sendspin pairing), because NVS moves
  from `0x390000` to `0xF90000`. Wi-Fi/API come from the config.

## Important pitfall: logs via CLI

Always run `timeout` **inside** the container:

```sh
podman exec <c> timeout 40 esphome logs /config/<device>.yaml --device <ip>
```

`timeout 40 podman exec ...` only kills the local client; the `esphome logs` process keeps running in the
container and holds an API connection. After a few of those the device rejects new API clients
(`Connection reset by peer (ReadFailedAPIError)`), also the Device Builder log view. Fix: restart the container.
Check open connections with `ss -tn dst <ip>`.

## Step 0: check the current partition table

The duplex/media player configs include the `debug` component, which prints the table when a log client connects
(Device Builder -> Logs, look for `Partition table:`):

```
app0  0  16  0x00010000 0x001C0000   <- small layout, migration needed
app1  0  17  0x001D0000 0x001C0000
nvs   1  2   0x00390000 0x0006D000
```

## Step 1: migration firmware (small, with partition access)

Create `<device>_migration.yaml` (gitignored via `*_migration.yaml`), with the device's `name`,
`friendly_name` and static IP from its device file:

```yaml
substitutions:
  name: "<device name from device file>"
  friendly_name: "<friendly name>"

esphome:
  name: "${name}"
  friendly_name: "${friendly_name}"
  min_version: 2026.10.0

esp32:
  board: esp32-s3-devkitc-1
  variant: esp32s3
  flash_size: 16MB # generates the same 16MB table and a 16MB bootloader as the real device config
  framework:
    type: esp-idf
    version: recommended

logger:
  baud_rate: 0

api:

packages:
  - !include wifi.yaml

ota: # defined here instead of including ota.yaml (avoids merging two OTA entries)
  platform: esphome
  password: !secret onju_ota
  allow_partition_access: true

wifi:
  manual_ip:
    static_ip: <ip>
    gateway: <gateway ip>
    subnet: 255.255.255.0
```

```sh
podman exec <c> esphome config  /config/<device>_migration.yaml   # optional check (output contains secrets, filter it)
podman exec <c> esphome compile /config/<device>_migration.yaml   # ~757kB; also builds bootloader.bin + partition-table.bin
podman exec <c> esphome upload  /config/<device>_migration.yaml --device <ip>
```

The migration config uses the same `name` as the device, so it shares the build folder
`.esphome/build/<name>/` with the real config. Compare `partitions.csv` there with the real config's
(expected: `app0`/`app1` `0x7C0000`, `nvs` `0x70000`).

Verify in the log (`podman exec <c> timeout 40 esphome logs /config/<device>_migration.yaml --device <ip>`):

```
esphome.ota:   Partition access allowed
esphome.ota:   Running app:
esphome.ota:     Partition address: 0x10000     <- equals new app0 offset: no copy needed (safest case)
esphome.ota:   Bootloader: ESP-IDF version unknown
```

If the running app is at `0x1D0000` (old app1), ESPHome copies it into a slot of the new table during step 3
(supported, but untested here). Optionally OTA the migration firmware once more so it runs from `0x10000`.

## Step 2: bootloader (16MB header)

```sh
podman exec <c> esphome upload --bootloader /config/<device>_migration.yaml --device <ip>
```

Takes < 1s. ESPHome stages the new bootloader in the free app slot, verifies it and only then copies
~20kB - that copy is the only critical window. Device reboots. Verify in the log: `Bootloader: ESP-IDF v5.5.6`.

Without this step, step 3 fails harmlessly with
`flash_parts: partition 2 invalid - offset 0x10000 size 0x7c0000 exceeds flash chip size 0x400000`
(`The partition table update could not be verified. No changes were made to the flash content.`).

## Step 3: partition table

```sh
podman exec <c> esphome upload --partition-table /config/<device>_migration.yaml --device <ip>
```

Log shows `Starting partition table update. DO NOT REMOVE POWER ...`, `Setting next boot partition to 0x10000`,
`Forcing a reboot`. Wait until the device answers on the OTA port again.

## Step 4: real firmware

Install the real device config normally (Device Builder, or `podman exec <c> esphome run /config/<device>.yaml --device <ip> --no-logs`).
`PERF` and all embedded sounds fit now (~2.4MB of 7.9MB).

Verify in the log:

```
safe_mode:   Bootloader rollback: supported
debug:   app0  0  16  0x00010000 0x007C0000
debug:   app1  0  17  0x007D0000 0x007C0000
debug:   nvs   1  2   0x00F90000 0x00070000
```

Then delete `<device>_migration.yaml` and re-pair Sendspin / restore settings if needed.

## Fallback

If anything fails in a way that leaves the device unbootable: flash once via USB with `firmware.factory.bin`
(Device Builder -> Install -> manual download -> factory format; OTA installs of 2026.10 skip the factory image).
