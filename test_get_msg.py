#!/usr/bin/env python3
"""Test: does mcrelay receive messages from Heltec?"""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from meshcore import EventType
from mcrelay.meshcore_utils import MeshCoreDirectClient

async def main():
    print("Connecting to /dev/ttyUSB0...")
    client = MeshCoreDirectClient("/dev/ttyUSB0", 115200)
    ok = await client.connect()
    if not ok:
        print("Connection failed!")
        return
    print("Connected. Sending get_msg (0x0a) every 3 s, 5x...")
    for i in range(5):
        result = await client.commands.get_msg(timeout=3.0)
        print(f"  [{i+1}] get_msg returned: {result.type}  payload={result.payload}")
        if result.type == EventType.CHANNEL_MSG_RECV:
            print(f"       -> channel {result.payload.get('channel_idx')}, text: {result.payload.get('text', '')[:50]}")
        elif result.type == EventType.CONTACT_MSG_RECV:
            print(f"       -> text: {result.payload.get('text', '')[:50]}")
        await asyncio.sleep(3)
    await client.disconnect()
    print("Done.")

if __name__ == "__main__":
    asyncio.run(main())
