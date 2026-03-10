# Matrix E2EE (Encrypted Rooms) Setup

For **encrypted Matrix rooms**, the bot must decrypt messages to relay them to MeshCore. Using only `access_token` from config (e.g. copied from Element) often fails because the E2EE key store is not properly initialized for that session.

**Solution:** Use `mcmgate auth login` to create `credentials.json`. This follows the same approach as [mmrelay](https://github.com/jeremiah-k/meshtastic-matrix-relay) and ensures the bot has a consistent session with a valid E2EE store.

## Quick Start

1. **Ensure config has homeserver and bot_user_id** (password is not stored in config):

   ```yaml
   matrix:
     homeserver: "https://matrix.example.com:8448"
     bot_user_id: "@meshtbot:matrix.example.com"
     # access_token can stay for fallback, but credentials.json takes priority
   ```

2. **Run interactive login:**

   ```bash
   mcmgate auth login
   ```

3. **Enter the bot password** when prompted (the password of the Matrix bot account).

4. **Restart mcmgate:**

   ```bash
   mcmgate
   # or: systemctl --user restart mcmgate
   ```

5. On first run after login, the bot syncs and receives E2EE keys.

6. **Encrypted groups – each sender must allow the bot:** For the bot to decrypt messages, each user who sends in the room must either:
   - **Verify the bot:** In the room, click on the bot (e.g. meshtbot) → Verify → complete emoji/decimal verification, or
  

## How It Works

- `mcmgate auth login` performs a full Matrix login with password (not just token).
- The response includes `access_token`, `device_id`, and `user_id`.
- These are saved to `~/.mcmgate/credentials.json`.
- When mcmgate starts, it checks for `credentials.json` first. If present, it uses those credentials instead of `access_token` from config.
- The E2EE store (`~/.mcmgate/store/`) is tied to the same `device_id`. After login, the first sync populates the store with room encryption keys from other users.
- Subsequent runs use `restore_login()` with the stored credentials, so the E2EE store remains consistent.

## When to Use

| Scenario | Use |
|----------|-----|
| **Unencrypted rooms** | `access_token` in config is fine |
| **Encrypted rooms** | `mcmgate auth login` + each sender verifies bot or allows unverified |
| **"undecryptable message" in logs** | Run `mcmgate auth login`, restart; then ask senders to verify bot |

## Credentials File

Location: `~/.mcmgate/credentials.json`

Permissions: `chmod 600` (recommended; the tool sets this on save).

Example:

```json
{
  "homeserver": "https://matrix.example.com:8448",
  "user_id": "@meshtbot:matrix.example.com",
  "device_id": "ABCDEFGHIJ",
  "access_token": "..."
}
```

**Security:** Never commit `credentials.json` or share it. It contains the full session token.

## Removing credentials.json

To use only `access_token` from config again:

```bash
rm ~/.mcmgate/credentials.json
```

Then restart mcmgate. It will fall back to config.
