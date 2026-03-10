#!/usr/bin/env python3
"""Check MeshCore contacts - does the gate device see its contacts?
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
    print(f"Connecting to MeshCore at {HOST}:{PORT}...")
    mc = await MeshCore.create_tcp(HOST, PORT)
    if not mc:
        print("Connection failed")
        return 1

    print("\n--- Self (gate device) ---")
    if mc.self_info:
        print(f"  Name: {mc.self_info.get('name', mc.self_info.get('adv_name', '?'))}")
        print(f"  Public key: {mc.self_info.get('public_key', '?')[:32]}...")
    else:
        print("  (no self_info)")

    print("\n--- Contacts (for meshcore_dm.contacts) ---")
    try:
        res = await mc.commands.get_contacts(timeout=5)
        if res and res.type == EventType.CONTACTS and res.payload:
            for pk, c in res.payload.items():
                name = c.get("adv_name", "").strip() or "(no name)"
                pubkey = c.get("public_key", pk)
                print(f"  {name}:")
                print(f"    pubkey: {pubkey}")
                print(f"    YAML:   - \"{pubkey}\"")
            if not res.payload:
                print("  (no contacts)")
        else:
            print("  get_contacts failed:", res)
    except Exception as e:
        print(f"  Error: {e}")

    await mc.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
