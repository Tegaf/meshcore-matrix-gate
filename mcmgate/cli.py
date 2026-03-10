"""CLI for MCMGate."""
import argparse
import asyncio
import sys

from mcmgate import __version__
from mcmgate.main import run_main


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="MCMGate - MeshCore Matrix bridge (LoRa mesh <-> Matrix open-source chat)"
    )
    parser.add_argument("--config", help="Path to config file", default=None)
    parser.add_argument("--version", action="store_true", help="Show version")
    parser.add_argument("--debug", action="store_true", help="Debug mode - trace loop/dedup")
    sub = parser.add_subparsers(dest="cmd", help="Commands")
    auth = sub.add_parser("auth", help="Matrix authentication (for E2EE)")
    auth_sub = auth.add_subparsers(dest="auth_cmd")
    auth_sub.add_parser("login", help="Interactive login – saves credentials.json for encrypted rooms")
    return parser.parse_args()


def main():
    args = parse_arguments()
    if args.version:
        print(f"MCMGate v{__version__}")
        return 0
    if args.debug:
        import os
        os.environ["MCMGATE_DEBUG"] = "1"
    if args.cmd == "auth" and args.auth_cmd == "login":
        from mcmgate.auth_utils import auth_login
        return asyncio.run(auth_login(args))
    return run_main(args)


if __name__ == "__main__":
    sys.exit(main())
