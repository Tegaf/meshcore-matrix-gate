# Changelog

## [0.2.0-beta1] - 2025-03-11

**Beta release.** Full 0.2.0 when tested and stable.

### Added
- **E2EE support:** `mcmgate auth login` for encrypted Matrix rooms (credentials.json)
- **MeshCore DM:** Device-to-device messages relay to/from Matrix
  - `contact_rooms`: map contact pubkey → Matrix room(s), multiple rooms per contact
  - `matrix_to_meshcore_only`: send-only rooms (Matrix→MeshCore, no receive)
  - Shared rooms: messages go to all matching contacts
- **Node key auto-fetch:** `node_public_key` and `node_private_key` optional – auto-fetched from device over TCP (supported firmware)
- **Announce on start:** `announce_on_start` (default false), `announce_skip_contacts`
- **Optional contacts:** Derived from `contact_rooms` and `matrix_to_meshcore_only`
- **Optional access_token:** When using credentials.json
- **Quick Setup** section in README
- **docs/CONFIG.md** – full config reference
- **docs/E2EE.md** – encrypted rooms setup
- **docs/DM_TROUBLESHOOTING.md** – DM decryption troubleshooting
- **docs/SECURITY.md** – security considerations
- Utility scripts: `export_node_key.py`, `check_contacts.py`, `send_dm.py`, `debug_meshcore_wifi.py`, `test_channel1.py`

### Changed
- Utility scripts read host/port from `~/.mcmgate/config.yaml` or env `MCMGATE_HOST`, `MCMGATE_PORT`
- `send_dm.py`: pubkey as required argument (no hardcoded values)
- `config.example.yaml`: generic placeholders, no personal data
- README: E2EE, MeshCore DM, security notes, chmod 600
- All code and docs in English

### Fixed
- Own-device echo detection: correct pubkey_prefix matching (was pk[:12], now startswith)
- Periodic Matrix re-join disabled (caused M_BAD_JSON with Conduit)

### Removed
- Hardcoded IPs, pubkeys, secrets from utility scripts
- Czech strings from codebase

## [0.2.0] - (planned)

Same as beta1, promoted when stable.
