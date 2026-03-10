# MCMGate Configuration Reference

Full reference for `~/.mcmgate/config.yaml`. All options and their behaviour.

---

## matrix

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `homeserver` | yes | - | Matrix homeserver URL (e.g. `https://matrix.example.com:8448`) |
| `access_token` | no* | - | Matrix access token. *Not needed when using `credentials.json` from `mcmgate auth login` (E2EE).* |
| `bot_user_id` | yes | - | Bot account user ID (e.g. `@gatebot:matrix.example.com`) |
| `ignore_unverified_devices` | no | false | When true, bot sends to unverified devices |
| `encryption_enabled` | no | true | Enable E2EE for encrypted rooms |

**Auth:** Prefer `mcmgate auth login` for encrypted rooms – creates `credentials.json` and takes priority over `access_token`.

---

## matrix_rooms

Matrix rooms mapped to MeshCore channels. Each room has its own channel.

```yaml
matrix_rooms:
  - id: "!roomId:matrix.example.com"
    meshcore_channel: 0   # Channel 0
  - id: "!anotherRoom:matrix.example.com"
    meshcore_channel: 1   # Channel 1
```

**Heltec WiFi limitation:** Matrix→MeshCore broadcasts only to channel 0. Rooms on channel 1+ will not receive messages (firmware accepts the command but does not broadcast). MeshCore→Matrix works for all channels. Use serial/USB for multi-channel Matrix→MeshCore.

---

## meshcore_dm

Device-to-device MeshCore messages (DM) relay to/from Matrix. **Serial/USB:** CONTACT_MSG_RECV. **WiFi:** RX_LOG_DATA payload_type 2, requires node keys for decryption (auto-fetched from device when omitted).

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `enabled` | yes | - | Enable MeshCore DM relay |
| `room_id` | no | - | Default room for DMs when no contact_rooms match. Fallback for unknown senders. |
| `contacts` | no | - | List of contact pubkeys (64 hex). **Optional** – derived from `contact_rooms` and `matrix_to_meshcore_only` when omitted. |
| `contact_rooms` | no | - | Map contact pubkey → room_id or [room_ids]. Defines where each contact's DMs go (MeshCore→Matrix) and which room sends to which contact (Matrix→MeshCore). |
| `matrix_to_meshcore_only` | no | - | Rooms that only send Matrix→MeshCore (do not receive). Map room_id → [contact pubkeys]. Messages from these rooms go to all listed contacts. |
| `announce_on_start` | no | **false** | When true, send "Bridge online" DM to contacts on startup. Must be explicitly enabled. |
| `announce_skip_contacts` | no | - | Pubkeys that do not receive announce (when `announce_on_start: true`) |
| `node_public_key` | no | - | Tegaf Gate public key (64 hex). **Optional** – auto-fetched from device `self_info`. |
| `node_private_key` | no | - | Tegaf Gate private key (128 hex). **Optional** – auto-fetched via `export_private_key` (works over TCP and USB on supported firmware). Add manually only if device returns DISABLED. |
| `reply_channel` | no | 0 | Fallback channel when room is only in meshcore_dm (not matrix_rooms) and DM fails |
| `recipients` | no | - | Matrix user IDs that receive a copy of each DM |

### contact_rooms

Map each contact to their Matrix room(s). A room can appear for multiple contacts (shared room) – messages from that room go to all matching contacts.

```yaml
contact_rooms:
  "contact1_pubkey_64_hex_chars":  # e.g. from check_contacts.py
    - "!roomA:matrix.example.com"
    - "!sharedRoom:matrix.example.com"
  "contact2_pubkey_64_hex_chars":
    - "!roomB:matrix.example.com"
    - "!sharedRoom:matrix.example.com"   # Shared – messages go to both
```

### Node key auto-fetch

When `node_public_key` and `node_private_key` are omitted, MCMGate fetches them from the device:

- **Public key:** from `self_info` (device reports it on connect)
- **Private key:** via `export_private_key` command (supported on TCP and USB by current Heltec firmware)

If the device returns DISABLED for export (older firmware), add both keys manually. Use `python3 export_node_key.py` to test; it prints the keys if export succeeds.

### matrix_to_meshcore_only

Rooms that **only send** Matrix→MeshCore. They do not receive MeshCore DMs. Useful for broadcast-style rooms where you write to multiple contacts but don't want their replies there.

```yaml
matrix_to_meshcore_only:
  "!sendOnlyRoom:matrix.example.com":
    - "pubkey_contact_1"
    - "pubkey_contact_2"
```

---

## meshcore

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `connection_type` | yes | - | `tcp`, `serial`, or `ble` |
| `host` | tcp | - | Heltec IP address |
| `port` | tcp | 4000 | TCP port (often 5000) |
| `serial_port` | serial | `/dev/ttyUSB0` | Serial device |
| `baudrate` | serial | 115200 | Baud rate |
| `ble_address` | ble | - | BLE device address |
| `meshnet_name` | no | "MeshCore" | Display name in Matrix |
| `broadcast_enabled` | no | true | Allow Matrix→MeshCore relay |
| `message_delay` | no | 2.2 | Seconds between queued messages |
| `channel_N_secret` | no | - | 32 hex chars for channel N. Same secret on all devices in channel. |
| `tcp_poll_enabled` | no | false | Enable TCP message polling if firmware doesn't push |

---

## matrix_dms (optional)

When a user DMs the bot, relay to MeshCore channel.

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `enabled` | yes | - | Enable Matrix DM relay |
| `default_channel` | no | 0 | Channel for DM messages |
| `recipients` | no | - | Map MeshCore channel messages to Matrix users |

---

## Recent Changes (Summary)

- **Room invites:** Manual accept in Element when bot sees invite – click Join.
- **Channel 1 (WiFi):** Heltec WiFi broadcasts only to channel 0; verified by testing.
- **contacts:** Optional – derived from `contact_rooms` and `matrix_to_meshcore_only`.
- **contact_rooms:** Supports multiple rooms per contact; shared rooms send to all matching contacts.
- **matrix_to_meshcore_only:** Send-only rooms, no MeshCore→Matrix.
- **announce_on_start:** Default `false` – must be explicitly enabled.
- **announce_skip_contacts:** Exclude specific contacts from "Bridge online".
- **access_token:** Optional when using `credentials.json` (mcmgate auth login).
- **node_public_key / node_private_key:** Optional – auto-fetched from device. Public key from `self_info`, private key via `export_private_key`. Works over TCP on supported firmware (Heltec WiFi). Add manually only if device returns DISABLED.
- **Periodic Matrix re-join:** Disabled – caused M_BAD_JSON with Conduit. Initial join on startup is sufficient.
