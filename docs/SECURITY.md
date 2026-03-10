# Security Considerations

## Credentials and Secrets

- **Config file** (`~/.mcmgate/config.yaml`): Contains `access_token`, `channel_N_secret`, and optionally `node_private_key`. Restrict permissions: `chmod 600 ~/.mcmgate/config.yaml`
- **Credentials file** (`~/.mcmgate/credentials.json`): Created by `mcmgate auth login`. Contains Matrix session token. Automatically set to `chmod 600` on save. Never commit or share.
- **E2EE store** (`~/.mcmgate/store/`): Matrix decryption keys. Keep private.
- **Database** (`~/.mcmgate/data/meshcore.sqlite`): Node names (longname/shortname). Low sensitivity.

## What MCMGate Does Not Do

- Never logs passwords, tokens, or private keys
- Uses YAML `SafeLoader` (no arbitrary code execution from config)
- SQL queries are parameterized (no injection)
- SSL/TLS for Matrix (certifi)
- Config `prefix_format` only accepts `{long}`, `{short}`, `{mesh}` placeholders

## export_node_key.py

Outputs the node private key to stdout. Run only in a private environment. Do not run over shared screens or unsecured SSH.

## Network

- MeshCore TCP: Connects to device on local network. Ensure MeshCore device is on a trusted network.
- Matrix: Uses HTTPS. For E2EE, verify the bot device in Element before sending sensitive messages.
