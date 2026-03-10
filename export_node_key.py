#!/usr/bin/env python3
"""Export Tegaf Gate node key for meshcore_dm decryption. Add output to config.
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
    print(f"Connecting to Tegaf Gate at {HOST}:{PORT}...")
    mc = await MeshCore.create_tcp(HOST, PORT)
    if not mc:
        print("Connection failed")
        return 1

    # Get self_info for public key
    pub = (mc.self_info or {}).get("public_key", "")
    if not pub or len(pub) != 64:
        print("No public_key in self_info")
        await mc.disconnect()
        return 1

    # Try export_private_key (on mc.commands directly, not .device)
    priv = None
    if hasattr(mc.commands, "export_private_key"):
        try:
            res = await mc.commands.export_private_key()
            print(f"export_private_key response: type={res.type if res else None} payload={res.payload if res else None}")
            if res and res.type == EventType.PRIVATE_KEY and res.payload.get("private_key"):
                priv = res.payload["private_key"].hex()
                print("Exported private key from device.")
            elif res and res.type == EventType.DISABLED:
                print("Device returned DISABLED – export blocked over TCP (security).")
            elif res and res.type == EventType.ERROR:
                print(f"Device returned ERROR: {res.payload}")
        except Exception as e:
            print(f"export_private_key failed: {e}")

    await mc.disconnect()

    if priv and len(priv) == 128:
        print("\nAdd to ~/.mcmgate/config.yaml under meshcore_dm:")
        print("  node_public_key:", repr(pub))
        print("  node_private_key:", repr(priv))
        return 0
    else:
        print("\nCould not get private key. Heltec WiFi may not support export_private_key.")
        print("You need to get the key from another source (e.g. device backup, MeshCore app).")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
