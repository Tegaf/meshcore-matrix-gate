# MeshCore DM Troubleshooting

## Why DM from MeshCore doesn't appear in Matrix

**Root cause:** DM decryption fails because the **node key** (private key) is not available.

### Verification

1. **DM packets ARE received** – Heltec WiFi firmware forwards payload_type=2 (TEXT_MSG) over TCP ✓
2. **Decrypt FAILS** – Log shows: `RX_LOG_DATA: payload_type=2 (DM) decrypt failed – check node_public_key/node_private_key in config`

### Why node key may be missing

- **Auto-fetch:** MCMGate fetches `node_public_key` from `self_info` and `node_private_key` via `export_private_key`. Supported firmware (including Heltec WiFi) allows this over TCP.
- **If device returns DISABLED:** Older firmware may block `export_private_key` over TCP. Add keys manually in that case.

### Solution: Add node key to config manually (only if auto-fetch fails)

1. **Get the private key** – try `python3 export_node_key.py` from the project root first (uses TCP, same host/port as config). If device returns DISABLED:
   - Connect MeshCore via USB and use meshcore-cli or Web Console (flasher.meshcore.co.uk)
   - Or use a backup from initial device setup

2. **Add to config** `~/.mcmgate/config.yaml`:

```yaml
meshcore_dm:
  enabled: true
  room_id: "!..."
  contacts:
    - "your_contact_pubkey_64_hex_chars"  # from check_contacts.py
  node_public_key: "gate_public_key_64_hex"   # from export_node_key.py or device
  node_private_key: "gate_private_key_128_hex"   # from export_node_key.py
```

3. **Restart mcmgate** – DM from Tegaf Mobile should then decrypt and relay to Matrix.

### Security note

Storing the private key in config saves it to disk. Ensure `~/.mcmgate/config.yaml` has restricted permissions (`chmod 600`).
