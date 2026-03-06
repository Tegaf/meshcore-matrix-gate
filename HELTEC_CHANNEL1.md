# Heltec Channel 1 – #meshcoregate Setup

Add this **second private channel** to your Heltec device for the #meshcoregate Matrix room. Each Matrix room uses its own MeshCore channel to avoid conflicts.

## Credentials for Heltec

| Field | Value |
|-------|-------|
| **Channel name** | `meshcoregate` |
| **Secret key (PSK)** | `6db22532fa5ecdcb7aca88b945e5f5d1` |

## How to add on Heltec

1. Open **Meshtastic** app (or your MeshCore client) and connect to the Heltec.
2. Go to **Channels** → **Add channel**.
3. Choose **Private channel**.
4. Enter:
   - **Name:** `meshcoregate`
   - **PSK / Secret key:** `6db22532fa5ecdcb7aca88b945e5f5d1`
5. Save. The channel will appear with a lock icon.

## MCMGate config

Already set in `~/.mcmgate/config.yaml`:

```yaml
channel_1_secret: "6db22532fa5ecdcb7aca88b945e5f5d1"
```

Restart MCMGate after adding the channel on Heltec:

```bash
systemctl --user restart mcmgate
```
