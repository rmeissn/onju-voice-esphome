#!/usr/bin/env python3
"""Press ESPHome buttons by name through the native API (only for an unencrypted API, as in this repo).

Runs inside the ESPHome Device Builder container, which already has aioesphomeapi (nothing to install):
  podman exec <container> python3 /config/tools/esphome_button.py <host> "Restart"
  podman exec <container> python3 /config/tools/esphome_button.py <host> "Start audio capture"
Used for the reboot test of the mic bug (docs/AUDIO_INVESTIGATION.md): restart, then a short capture for the raw check.
"""

import asyncio
import sys

from aioesphomeapi import APIClient, ButtonInfo


async def press(host: str, names: list[str]) -> int:
    client = APIClient(host, 6053, None)
    await client.connect(login=True)
    try:
        entities, _ = await client.list_entities_services()
        buttons = {e.name: e for e in entities if isinstance(e, ButtonInfo)}
        for name in names:
            if name not in buttons:
                print(f"unknown button {name!r}, known: {sorted(buttons)}")
                return 1
            button = buttons[name]
            client.button_command(button.key, device_id=getattr(button, "device_id", 0))
            print(f"pressed {name!r}")
        await asyncio.sleep(0.5)  # let the command go out before disconnecting
    finally:
        await client.disconnect()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    raise SystemExit(asyncio.run(press(sys.argv[1], sys.argv[2:])))
