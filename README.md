# MCMGate - MeshCore Matrix Gate

MeshCore Matrix bridge. Inspired by mmrelay (Meshtastic), adapted for MeshCore protocol. GPL-3.0.

Tested on Raspberry Pi (Matrix) and Heltec V3 (MeshCore).

## Screenshots

![Heltec room](docs/heltec-room.png)


## Installation

Requires Python 3.10+. Use a virtual environment or pipx:

```bash
git clone https://github.com/Tegaf/meshcore-matrix-gate.git
cd meshcore-matrix-gate
python -m venv .venv && .venv/bin/pip install -e .
# or: pipx install -e .
```

## Getting Started

### 1. Matrix Setup

You need a **bot account** for the relay. Create a dedicated Matrix account (e.g. `@mcmgate-bot:matrix.org`):

1. Open [Element Web](https://app.element.io/) in a **private/incognito window** (Ctrl+Shift+N in Chrome, Ctrl+Shift+P in Firefox)
2. Create an account on matrix.org or your homeserver
3. Create a room for the bridge (or use an existing one)
4. **Room ID**: Room settings → Advanced → Room ID (e.g. `!abc123:matrix.org`)
5. **Access token**: Settings → Help & About → scroll to bottom, expand Access Token and copy
6. **Close the window** – do not log out. Logging out invalidates the token. After closing, the session stays active.

> **Room encryption:** Matrix rooms with E2EE are not currently supported. The bot cannot decrypt messages in encrypted rooms, so Matrix→MeshCore relay does not work. Messages in both directions may fail. Use **unencrypted** rooms.

### 2. Configuration

Config lives in `~/.mcmgate/config.yaml` (separate from the project). From the project directory:

```bash
mkdir -p ~/.mcmgate
cp config.example.yaml ~/.mcmgate/config.yaml
```

Edit `~/.mcmgate/config.yaml` with your Matrix token, room ID, and MeshCore settings.

**Serial (USB):**

```yaml
matrix:
  homeserver: "https://matrix.example.com:8448"
  access_token: "your_token"
  bot_user_id: "@yourbot:matrix.example.com"

matrix_rooms:
  - id: "!roomId:matrix.example.com"
    meshcore_channel: 0  # Channel 0 = broadcast
  - id: "!anotherRoom:matrix.example.com"
    meshcore_channel: 1  # e.g. separate private channel on Heltec

meshcore:
  connection_type: serial
  serial_port: "/dev/ttyUSB0"
  baudrate: 115200
  meshnet_name: "My MeshCore"
  broadcast_enabled: true
  message_delay: 2.2
  channel_0_secret: "your_channel_0_secret_32_hex_chars"  # for private channel
  channel_1_secret: "your_channel_1_secret_32_hex_chars"  # for private channel
```

**TCP (WiFi firmware – Heltec with WiFi):**

```yaml
matrix:
  homeserver: "https://matrix.example.com:8448"
  access_token: "your_token"
  bot_user_id: "@yourbot:matrix.example.com"

matrix_rooms:
  - id: "!roomId:matrix.example.com"
    meshcore_channel: 0

meshcore:
  connection_type: tcp
  host: "192.168.1.100"   # Heltec IP (check your router)
  port: 5000
  meshnet_name: "My MeshCore"
  broadcast_enabled: true
  message_delay: 2.2
  channel_0_secret: "your_channel_0_secret_32_hex_chars"  # for private channel
  # tcp_poll_enabled: false  # enable if WiFi firmware doesn't push RX_LOG_DATA
```

### Platform Notes (Serial)

| Platform | Serial port example |
|----------|----------------------|
| Linux | `/dev/ttyUSB0`, `/dev/ttyACM0` |
| macOS | `/dev/cu.usbserial-*`, `/dev/cu.usbmodem*` |
| Windows | `COM3`, `COM4` |

`channel_0_secret` must be 32 hex characters (16 bytes). Use the same secret on all MeshCore devices in the channel.

**Multiple channels:** Supported in direction **MeshCore → Matrix** (each room can receive from its own channel). In the opposite direction **Matrix → MeshCore**, only channel 0 is supported – Heltec WiFi firmware does not broadcast to other channels. Create channel 1 on Heltec and add `channel_1_secret` to config.

## Running

If you used venv, run from the project directory (or use the full path to the binary):

```bash
cd meshcore-matrix-gate
.venv/bin/mcmgate
```

If you used pipx, run from anywhere:

```bash
mcmgate
```

Config is always read from `~/.mcmgate/config.yaml`.

## Systemd (optional)

For auto-start on boot:

1. Copy the service file (adjust path if you cloned elsewhere):

```bash
mkdir -p ~/.config/systemd/user
cp ~/meshcore-matrix-gate/mcmgate.service.example ~/.config/systemd/user/mcmgate.service
nano ~/.config/systemd/user/mcmgate.service
```

2. Replace `/path/to/meshcore-matrix-gate` with your actual path (e.g. `/home/USERNAME/meshcore-matrix-gate`):

```
ExecStart=/home/USERNAME/meshcore-matrix-gate/.venv/bin/mcmgate
WorkingDirectory=/home/USERNAME/meshcore-matrix-gate
```

3. Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable mcmgate
systemctl --user start mcmgate
```

4. Check status: `systemctl --user status mcmgate`

## Credits

MCMGate is inspired by [mmrelay](https://github.com/jeremiah-k/meshtastic-matrix-relay) (Meshtastic Matrix Relay) by Geoff Whittington, Jeremiah K., and contributors. Bridges MeshCore LoRa mesh with Matrix (open-source federated chat). Adapted for MeshCore protocol. GPL-3.0.

For more Matrix setup details (Element, encrypted rooms), see [mmrelay Getting Started](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Getting-Started-With-Matrix-&-MM-Relay).


