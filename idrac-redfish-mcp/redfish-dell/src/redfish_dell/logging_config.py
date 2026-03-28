import os
import logging

def configure_logging() -> None:
    """Configure the root logger based on the `LOG_LEVEL` env var.

    This mirrors the behavior previously present in `fastmcp_server.py`.
    Calling this function multiple times is safe; it will only add a
    handler if none are present and will always set the root level.
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler()
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(level)
        root.addHandler(handler)
    else:
        root.setLevel(level)