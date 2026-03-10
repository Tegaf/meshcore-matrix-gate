"""Matrix auth login (mmrelay-style) for E2EE support.
Use `mcmgate auth login` to create credentials.json – enables decryption in encrypted rooms."""
import asyncio
import getpass
import os
import ssl

import certifi
from nio import AsyncClient, AsyncClientConfig
from nio.responses import LoginError

from mcmgate.config import get_base_dir, get_credentials_path, load_config, save_credentials


async def auth_login(args=None) -> int:
    """Interactive Matrix login. Saves credentials.json for E2EE. Returns 0 on success."""
    cfg = load_config(args=args)
    matrix = (cfg or {}).get("matrix", {})
    homeserver = matrix.get("homeserver", "").strip()
    bot_user_id = matrix.get("bot_user_id", "").strip()

    if not homeserver:
        homeserver = input("Matrix homeserver URL (e.g. https://matrix.example.com:8448): ").strip()
    if not bot_user_id:
        bot_user_id = input("Bot user ID (e.g. @bot:matrix.example.com): ").strip()

    if not homeserver or not bot_user_id:
        print("Error: homeserver and bot_user_id required")
        return 1

    # Extract localpart from user_id
    if not bot_user_id.startswith("@"):
        print("Error: user_id must start with @")
        return 1
    localpart = bot_user_id[1:].split(":")[0] if ":" in bot_user_id else bot_user_id[1:]

    password = getpass.getpass("Bot password: ")
    if not password:
        print("Error: password required")
        return 1

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    client = AsyncClient(
        homeserver=homeserver,
        user=bot_user_id,
        store_path=os.path.join(get_base_dir(), "store"),
        config=AsyncClientConfig(encryption_enabled=True),
        ssl=ssl_ctx,
    )

    try:
        resp = await asyncio.wait_for(client.login(password), timeout=30.0)
    except asyncio.TimeoutError:
        print("Login timed out")
        await client.close()
        return 1
    except Exception as e:
        print(f"Login failed: {e}")
        await client.close()
        return 1

    if isinstance(resp, LoginError):
        print(f"Login error: {resp.message}")
        await client.close()
        return 1

    access_token = getattr(resp, "access_token", None)
    device_id = getattr(resp, "device_id", "")
    user_id = getattr(resp, "user_id", bot_user_id)

    if not access_token:
        print("Login succeeded but no access_token in response")
        await client.close()
        return 1

    creds = {
        "homeserver": homeserver,
        "user_id": user_id,
        "device_id": device_id or "",
        "access_token": access_token,
    }
    if save_credentials(creds):
        print(f"Credentials saved to {get_credentials_path()}")
        print("E2EE store will be populated on first sync. Restart mcmgate.")
    else:
        print("Failed to save credentials")
        return 1

    await client.close()
    return 0
