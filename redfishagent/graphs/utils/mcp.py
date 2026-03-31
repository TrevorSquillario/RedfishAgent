from typing import Annotated, TypedDict, Optional, Any
from redfishagent.utils.logging import setup_logger
import os
from urllib.parse import urlparse

logger = setup_logger(__name__)

def build_mcp_servers_map(mcp_entries: Optional[list[Any]]) -> dict:
    """Turn a list of MCP entries into the mapping expected by MultiServerMCPClient.

    Each entry may be:
    - a dict with a full server config (passed through)
    - a string URL (http/https)
    - a string path to a .py file (will be launched with `python` + stdio)
    """
    if not mcp_entries: 
        return {}

    servers = {}
    for idx, entry in enumerate(mcp_entries):
        name = None
        cfg = None
        if isinstance(entry, dict):
            # allow explicit name in the dict
            name = entry.get("name") or f"mcp-{idx}"
            cfg = {k: v for k, v in entry.items() if k != "name"}
        elif isinstance(entry, str):
            name = f"mcp-{idx}"
            entry_str = entry.strip()
            # support file:// URIs
            if entry_str.startswith("file://"):
                path = urlparse(entry_str).path
                path = os.path.expanduser(path)
                if os.path.exists(path):
                    cfg = {"command": "python", "args": [path], "transport": "stdio"}
                else:
                    logger.warning("MCP file not found: %s", path)
                    continue
            # http(s) endpoints
            elif entry_str.startswith(("http://", "https://")):
                cfg = {"url": entry_str, "transport": "http"}
            else:
                # treat local .py files or existing filesystem paths as stdio commands
                maybe_path = os.path.expanduser(entry_str)
                if entry_str.endswith(".py") or os.path.exists(maybe_path):
                    chosen = maybe_path if os.path.exists(maybe_path) else entry_str
                    cfg = {"command": "python", "args": [chosen], "transport": "stdio"}
                else:
                    # fallback to treating as URL
                    cfg = {"url": entry_str, "transport": "http"}
        else:
            # unsupported type; skip
            logger.warning("Skipping unsupported MCP entry type: %s", type(entry))
            continue

        servers[name] = cfg

    return servers