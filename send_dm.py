#!/usr/bin/env python3
"""Send a DM to a contact directly (no mcmgate bridge).
Usage: python send_dm.py <contact_pubkey_64_hex> [message]
Reads host/port from ~/.mcmgate/config.yaml or env MCMGATE_HOST, MCMGATE_PORT."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from meshcore import MeshCore, EventType

try:
    from mcmgate.config import load_config
    _cfg = load_config()
    _mc = _cfg.get("meshcore", {})
    HOST = _mc.get("host") or os.environ.get("MCMGATE_HOST", "192.168.1.100")
    PORT = int(_mc.get("port") or os.environ.get("MCMGATE_PORT", "5000"))
except Exception:
    HOST = os.environ.get("MCMGATE_HOST", "192.168.1.100")
    PORT = int(os.environ.get("MCMGATE_PORT", "5000"))


async def main():
    if len(sys.argv) < 2:
        print("Usage: python send_dm.py <contact_pubkey_64_hex> [message]")
        return 1
    pubkey = sys.argv[1].strip()
    if len(pubkey) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pubkey):
        print("Error: contact pubkey must be 64 hex characters")
        return 1
    msg = sys.argv[2] if len(sys.argv) > 2 else "Hello from script"
    print(f"Connecting to MeshCore at {HOST}:{PORT}...")
    mc = await MeshCore.create_tcp(HOST, PORT)
    if not mc:
        print("Connection failed")
        return 1
    print(f"Sending DM to contact: {msg!r}")
    result = await mc.commands.send_msg(pubkey, msg)
    if result.type == EventType.ERROR:
        print(f"Failed: {result.payload}")
        return 1
    print("Sent.")
    await mc.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
