# MCRelay - MeshCore Matrix Relay

Fork of mmrelay adapted for **MeshCore** protocol. Bridges MeshCore mesh networks to Matrix chat rooms.

## Installation

```bash
cd mcrelay
pip install -e .
# or: pipx install -e .
```

## Configuration

Copy `config.example.yaml` to `~/.mcrelay/config.yaml` and fill in your values:

```bash
mkdir -p ~/.mcrelay
cp config.example.yaml ~/.mcrelay/config.yaml
```

Edit `~/.mcrelay/config.yaml`:

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

## Running

```bash
mcrelay
```

## Systemd (optional)

For auto-start on boot, copy `mcrelay.service.example` to `~/.config/systemd/user/mcrelay.service`, edit paths, then:

```bash
systemctl --user daemon-reload
systemctl --user enable mcrelay
systemctl --user start mcrelay
```

## Credits

MCRelay is a fork of [mmrelay](https://github.com/jeremiah-k/meshtastic-matrix-relay) (Meshtastic Matrix Relay) by Geoff Whittington, Jeremiah K., and contributors. Adapted for MeshCore protocol. Licensed under GPL-3.0.

## Migration from mmrelay (Meshtastic)

If you have existing `~/.mmrelay/config.yaml`, you can copy it and change:
- `meshtastic` → `meshcore`
- `meshtastic_channel` → `meshcore_channel`
- `connection_type: tcp` + `host` → `connection_type: serial` + `serial_port: /dev/ttyUSB0`
