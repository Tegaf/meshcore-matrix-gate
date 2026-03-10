#!/usr/bin/env python3
"""Test send_chan_msg(1) directly to MeshCore device.
Reads host/port from ~/.mcmgate/config.yaml or env MCMGATE_HOST, MCMGATE_PORT."""
import asyncio
import os
from pathlib import Path

# Allow running from project root
import sys
sys.path.insert(0, str(Path(__file__).parent))

from meshcore import MeshCore

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
    print(f"Connecting to MeshCore at {HOST}:{PORT}...")
    mc = await MeshCore.create_tcp(HOST, PORT)
    if not mc:
        print("Connection failed")
        return
    print("Sending to channel 1...")
    result = await mc.commands.send_chan_msg(1, "test channel 1")
    print(f"Result: {result.type} {getattr(result, 'payload', {})}")
    await mc.disconnect()
    print("Done. Check the mobile device – did the message arrive?")

if __name__ == "__main__":
    asyncio.run(main())
