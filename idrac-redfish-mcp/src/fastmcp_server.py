from typing import Any, Dict, List, Optional
import os
import json
import logging
from fastmcp import FastMCP
from starlette.responses import JSONResponse

from redfish_dell import DellRedfishClient
from utils.logging_config import configure_logging

configure_logging()
logger = logging.getLogger("idrac_redfish_mcp")

mcp = FastMCP("iDRAC Redfish MCP")


def debug_log_params(func_name: str, params: Dict[str, Any], mask_fields: Optional[List[str]] = None) -> None:
    """
    Log parameters at debug level while redacting sensitive fields.

    - func_name: short name to include in the log line
    - params: dictionary of parameter names -> values
    - mask_fields: list of keys (case-insensitive) to redact; defaults to common password keys
    """
    if mask_fields is None:
        mask_fields = ["password", "pass", "pwd"]

    safe: Dict[str, Any] = {}
    mask_set = {m.lower() for m in mask_fields}
    for k, v in params.items():
        if k.lower() in mask_set:
            safe[k] = "***REDACTED***"
        else:
            safe[k] = v

    # Try to render as JSON for compactness; fall back to plain repr on failure
    try:
        logger.debug("%s params: %s", func_name, json.dumps(safe, default=str))
    except Exception:
        logger.debug("%s params (safe): %r", func_name, safe)

def _get_url(host: str, port: int):
    if port and port != 443:
        base_url = f"https://{host}:{port}"
    else:
        base_url = f"https://{host}"

    return base_url

@mcp.tool
def get_lc_logs(
    host: str,
    port: int = 443,
    verify: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    severity: Optional[str] = None,
    top: Optional[int] = 10,
    skip: Optional[int] = None,
) -> str:
    """
    Retrieve iDRAC lifecycle (LC) logs from a Dell server.

    Args:
        host: The IP address or hostname of the iDRAC.
        port: The HTTPS port for the Redfish API (default 443).
        verify: Whether to verify SSL certificates.
        username: iDRAC username for basic authentication.
        password: iDRAC password for basic authentication.
        start_date: Start filter in this format: YYYY-MM-DDTHH:MM:SS-offset (example: 2023-03-14T10:10:10-05:00)
        end_date: End filter in this format: YYYY-MM-DDTHH:MM:SS-offset (example: 2023-03-14T10:10:10-05:00)
        severity: Filter logs by severity, options include: informational, warning and critical
        top: Optional integer to limit returned entries (maps to $top)
        skip: Optional integer to skip initial entries (maps to $skip)
    """
    if not host:
        logger.error("missing 'host' in params")
        raise ValueError("missing 'host' in params")

    url = _get_url(host=host, port=port)
    # Debug-log the incoming parameters, redact sensitive fields (password).
    debug_log_params("get_lc_logs", {
        "host": host,
        "port": port,
        "verify": verify,
        "username": username,
        "password": password,
        "start_date": start_date,
        "end_date": end_date,
        "severity": severity,
        "top": top,
        "skip": skip,
    })

    client = DellRedfishClient(base_url=url, username=username, password=password)
    logs = client.get_lifecycle_logs(
        start_date=start_date,
        end_date=end_date,
        severity=severity,
        top=top,
        skip=skip,
    )
    return json.dumps(logs)

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    return JSONResponse({"status": "ok"})

if __name__ == "__main__":
    # Run the MCP server over TCP so the container keeps running
    # and listens on port 8080 for incoming MCP connections.
    mcp.run(transport="http", host="0.0.0.0", port=8080)


__all__ = ["mcp"]
