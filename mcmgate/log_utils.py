"""Logging utilities."""
import logging
from rich.logging import RichHandler

config = None


def get_logger(name="mcmgate"):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = RichHandler(show_path=False)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
