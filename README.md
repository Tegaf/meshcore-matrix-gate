# MCMRelay - MeshCore Matrix Relay

Inspirováno mmrelay, adaptováno pro **MeshCore** protokol. Bridges MeshCore mesh networks to Matrix chat rooms.

Tested on Raspberry Pi (Matrix) and Heltec V3 (MeshCore).

## Screenshots

![Heltec room](docs/heltec-room.png)


## Installation

Requires Python 3.10+. Use a virtual environment or pipx:

```bash
cd mcmrelay
python -m venv .venv && .venv/bin/pip install -e .
# or: pipx install -e .
```

## Getting Started

### 1. Matrix Setup

You need a **bot account** for the relay. Create a dedicated Matrix account (e.g. `@mcmrelay-bot:matrix.org`):

1. Open [Element Web](https://app.element.io/) v **anonymním okně** (Ctrl+Shift+N v Chrome, Ctrl+Shift+P ve Firefoxu)
2. Vytvořte účet na matrix.org nebo vašem homeserveru
3. Vytvořte místnost pro bridge (nebo použijte existující)
4. **Room ID**: Nastavení místnosti → Advanced → Room ID (např. `!abc123:matrix.org`)
5. **Access token**: Nastavení → Help & About → na konec, rozbalte Access Token a zkopírujte
6. **Zavřete okno** – neodhlašujte se. Odhlášení token zneplatní. Po zavření okna session zůstane aktivní.

### 2. Configuration

Config is looked up in order: `--config` path, `~/.mcmrelay/config.yaml` (Linux/macOS), `./config.yaml`. On Windows: platform app data directory.

Copy `config.example.yaml` to your config directory:

```bash
mkdir -p ~/.mcmrelay
cp config.example.yaml ~/.mcmrelay/config.yaml
```

Edit `~/.mcmrelay/config.yaml`:

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
    meshcore_channel: 1

meshcore:
  connection_type: serial
  serial_port: "/dev/ttyUSB0"
  baudrate: 115200
  meshnet_name: "My MeshCore"
  broadcast_enabled: true
  message_delay: 2.2
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

## Running

```bash
mcmrelay
```

## Systemd (optional)

For auto-start on boot, copy `mcmrelay.service.example` to `~/.config/systemd/user/mcmrelay.service`, edit paths, then:

```bash
systemctl --user daemon-reload
systemctl --user enable mcmrelay
systemctl --user start mcmrelay
```

## Credits

MCMRelay je inspirováno [mmrelay](https://github.com/jeremiah-k/meshtastic-matrix-relay) (Meshtastic Matrix Relay) od Geoff Whittingtona, Jeremiah K. a přispěvatelů. Adaptováno pro MeshCore protokol. Licence GPL-3.0.

For more Matrix setup details (Element, encrypted rooms), see [mmrelay Getting Started](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Getting-Started-With-Matrix-&-MM-Relay).

## Migration

**From mcrelay (previous name):** Rename `~/.mcrelay` to `~/.mcmrelay` and reinstall: `pip install -e .` (or `pipx install -e .`).

**From mmrelay (Meshtastic):** If you have existing `~/.mmrelay/config.yaml`, you can copy it and change:
- `meshtastic` → `meshcore`
- `meshtastic_channel` → `meshcore_channel`
- `connection_type: tcp` + `host` → `connection_type: serial` + `serial_port: /dev/ttyUSB0`
