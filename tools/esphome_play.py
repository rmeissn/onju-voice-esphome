#!/usr/bin/env python3
"""Play a media URL on an ESPHome media player through the native API, optionally repeated (unencrypted API only).

Runs inside the ESPHome Device Builder container (it has aioesphomeapi):
  podman exec <container> python3 /config/tools/esphome_play.py <host> "audio-file://wakeup" [repeat] [interval_s]
Plays as announcement on the first media player whose name is "Media Player". Used to put sound on the mics during a
capture (mic bug test, docs/AUDIO_INVESTIGATION.md) without a human.
"""

import asyncio
import sys

from aioesphomeapi import APIClient, MediaPlayerInfo


async def play(host: str, url: str, repeat: int, interval: float) -> int:
    client = APIClient(host, 6053, None)
    await client.connect(login=True)
    try:
        entities, _ = await client.list_entities_services()
        players = [e for e in entities if isinstance(e, MediaPlayerInfo) and e.name == "Media Player"]
        if not players:
            print("no media player named 'Media Player'")
            return 1
        player = players[0]
        for _ in range(repeat):
            client.media_player_command(player.key, media_url=url, announcement=True,
                                        device_id=getattr(player, "device_id", 0))
            await asyncio.sleep(interval)
        print(f"played {url!r} {repeat}x")
    finally:
        await client.disconnect()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    reps = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    every = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0
    raise SystemExit(asyncio.run(play(sys.argv[1], sys.argv[2], reps, every)))
