#!/usr/bin/env python3
"""Debug script: connect to MeshCore WiFi and log all events for 60 seconds.
Reads host/port and channel secrets from ~/.mcmgate/config.yaml or env."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from meshcore import MeshCore

try:
    from mcmgate.config import load_config
    _cfg = load_config()
    _mc = _cfg.get("meshcore", {})
    HOST = _mc.get("host") or os.environ.get("MCMGATE_HOST", "192.168.1.100")
    PORT = int(_mc.get("port") or os.environ.get("MCMGATE_PORT", "5000"))
    CHANNEL_SECRETS = {i: _mc[f"channel_{i}_secret"] for i in range(5) if _mc.get(f"channel_{i}_secret")}
except Exception:
    HOST = os.environ.get("MCMGATE_HOST", "192.168.1.100")
    PORT = int(os.environ.get("MCMGATE_PORT", "5000"))
    CHANNEL_SECRETS = {}


async def main():
    print(f"Connecting to MeshCore at {HOST}:{PORT}...")
    try:
        mc = await MeshCore.create_tcp(HOST, PORT)
    except Exception as e:
        print(f"Connection failed: {e}")
        return 1

    if not mc:
        print("Connection returned None")
        return 1

    # Register channels for LOG_DATA decryption
    channel_secrets = {i: CHANNEL_SECRETS.get(i) for i in range(5) if CHANNEL_SECRETS.get(i)}
    if mc._reader and channel_secrets:
        channels = mc._reader.channels
        next_slot = 0
        from hashlib import sha256
        for idx, secret_hex in channel_secrets.items():
            try:
                secret = bytes.fromhex(secret_hex)
                ch_hash = sha256(secret).hexdigest()[0:2]
                channels[next_slot] = {
                    "channel_idx": idx,
                    "channel_name": f"channel_{idx}",
                    "channel_secret": secret,
                    "channel_hash": ch_hash,
                }
                print(f"  Channel {idx}: hash={ch_hash}")
                next_slot += 1
            except (ValueError, TypeError) as e:
                print(f"  Channel {idx} invalid: {e}")
        for observed_hash in ("34", "00"):
            for idx, secret_hex in channel_secrets.items():
                try:
                    secret = bytes.fromhex(secret_hex)
                    channels[next_slot] = {
                        "channel_idx": idx,
                        "channel_name": f"channel_{idx}",
                        "channel_secret": secret,
                        "channel_hash": observed_hash,
                    }
                    next_slot += 1
                except (ValueError, TypeError):
                    pass

    print("Connected. Listening for 60 seconds...")
    print("Send a DM from a contact to the gate device NOW to test payload_type=2.\n")

    events_received = []
    payload_types_seen = set()

    def on_event(event):
        events_received.append(event)
        payload = event.payload if hasattr(event, "payload") else {}
        pt = payload.get("payload_type")
        pt_name = payload.get("payload_typename", "?")
        msg = payload.get("message", payload.get("text", ""))
        if event.type.name == "rx_log_data":
            payload_types_seen.add(pt)
            if pt == 2:
                print(f"  >>> DM RECEIVED! payload_type=2 (TEXT_MSG) path_len={payload.get('path_len')} "
                      f"payload_len={len(payload.get('payload', '')) if isinstance(payload.get('payload'), str) else 0}")
            else:
                print(f"  RX_LOG_DATA: payload_type={pt} ({pt_name}) path_len={payload.get('path_len')} "
                      f"chan_hash={payload.get('chan_hash')} message={msg[:50] if msg else '(empty)'!r}")
        else:
            print(f"  {event.type.name}: {payload}")

    mc.subscribe(None, on_event)  # all events

    await asyncio.sleep(60)

    await mc.disconnect()
    print(f"\nDone. Received {len(events_received)} events.")
    print(f"Payload types seen: {sorted(payload_types_seen)}")
    if 2 not in payload_types_seen:
        print("WARNING: payload_type=2 (DM) was NOT received! Heltec firmware may not forward DM packets over TCP.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
