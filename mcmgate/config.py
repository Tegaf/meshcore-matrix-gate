"""Configuration for MCMGate."""
import os
import sys
import platformdirs
import yaml
from yaml.loader import SafeLoader

APP_NAME = "mcmgate"
APP_AUTHOR = None
custom_data_dir = None


def get_base_dir():
    if custom_data_dir:
        return custom_data_dir
    if sys.platform in ["linux", "darwin"]:
        return os.path.expanduser(os.path.join("~", "." + APP_NAME))
    return platformdirs.user_data_dir(APP_NAME, APP_AUTHOR)


def get_config_paths(args=None):
    paths = []
    if args and args.config:
        paths.append(os.path.abspath(args.config))
    user_config_dir = get_base_dir()
    os.makedirs(user_config_dir, exist_ok=True)
    paths.append(os.path.join(user_config_dir, "config.yaml"))
    paths.append(os.path.join(os.getcwd(), "config.yaml"))
    return paths


def load_config(config_file=None, args=None):
    paths = get_config_paths(args)
    for path in paths:
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    return yaml.load(f, Loader=SafeLoader) or {}
            except Exception as e:
                import logging
                logging.getLogger("mcmgate").error(f"Config load error: {e}")
                return {}
    return {}


CREDENTIALS_FILENAME = "credentials.json"


def get_credentials_path():
    """Path to Matrix credentials.json (mmrelay-style, for E2EE)."""
    return os.path.join(get_base_dir(), CREDENTIALS_FILENAME)


def load_credentials():
    """Load Matrix credentials from credentials.json. Returns dict or None."""
    path = get_credentials_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            import json
            return json.load(f)
    except Exception:
        return None


def save_credentials(creds: dict) -> bool:
    """Save Matrix credentials to credentials.json. Returns True on success."""
    path = get_credentials_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            import json
            json.dump(creds, f, indent=2)
        os.chmod(path, 0o600)
        return True
    except Exception:
        return False
