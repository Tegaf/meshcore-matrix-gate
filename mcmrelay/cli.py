"""CLI for MCMRelay."""
import argparse
import sys

from mcmrelay import __version__
from mcmrelay.main import run_main


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="MCMRelay - MeshCore Matrix Relay (MeshCore <-> Matrix bridge)"
    )
    parser.add_argument("--config", help="Path to config file", default=None)
    parser.add_argument("--version", action="store_true", help="Show version")
    parser.add_argument("--debug", action="store_true", help="Debug mode - trace loop/dedup")
    return parser.parse_args()


def main():
    args = parse_arguments()
    if args.version:
        print(f"MCMRelay v{__version__}")
        return 0
    if args.debug:
        import os
        os.environ["MCMRELAY_DEBUG"] = "1"
    return run_main(args)


if __name__ == "__main__":
    sys.exit(main())
