from typing import Any, Dict, List, Optional
import os
import json
import logging
import base64
from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from starlette.responses import JSONResponse

from redfish_dell.client import DellRedfishClient
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
def get_error_and_event_registry(
    message_ids: List[str],
    host: str,
    port: int = 443,
    verify: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """
    Retrieve the iDRAC Message Registry (EEMI) mapping or specific message entries.

    This will first attempt to load the bundled `files/eemi.json` registry shipped
    with this package. If that file is present and valid, its `Messages` mapping
    (or the specific entries for `message_ids`) will be returned. If not present
    or loading fails, the code falls back to querying the target iDRAC via Redfish.

    Args:
        message_ids: List of message ID strings to retrieve (e.g. ['CPU0001']).
        host: The IP address or hostname of the iDRAC.
        port: Optional HTTPS port for the Redfish API (default 443).
        verify: Optional Whether to verify SSL certificates when contacting iDRAC.
        username: Optional iDRAC username for basic authentication.
        password: Optional iDRAC password for basic authentication.
    """
    if not host:
        logger.error("missing 'host' in params")
        raise ValueError("missing 'host' in params")

    url = _get_url(host=host, port=port)
    debug_log_params("get_error_and_event_registry", {
        "host": host,
        "port": port,
        "verify": verify,
        "username": username,
        "password": password,
        "message_ids": message_ids,
    })

    # Try bundled local EEMI file first
    local_path = os.path.join(os.path.dirname(__file__), "files", "eemi.json")
    try:
        if os.path.exists(local_path):
            logger.info("loading local EEMI registry from %s", local_path)
            with open(local_path, "r") as fh:
                data = json.load(fh)
            messages = data.get("Messages") or {}
            # If specific message_ids were requested, return a mapping of
            # message_id -> entry (None if not found). If no message_ids
            # were provided, return the entire registry.
            if message_ids:
                result_map = {mid: messages.get(mid) for mid in message_ids}
                return json.dumps(result_map)
            return json.dumps(messages)
    except Exception:
        logger.exception("failed to load local EEMI registry %s", local_path)

    # Fallback to querying the iDRAC Redfish registry
    client = DellRedfishClient(base_url=url, username=username, password=password)
    results = {}
    for message_id in message_ids:
        try:
            results[message_id] = client.get_error_and_event_registry(message_id=message_id)
        except Exception:
            # Preserve failures as None but continue collecting other IDs
            logger.exception("failed to fetch registry entry for %s", message_id)
            results[message_id] = None

    return json.dumps(results)

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


@mcp.tool
def get_idrac_attributes(
    host: str,
    port: int = 443,
    verify: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """
    Retrieve iDRAC configuration attributes.

    Args:
        host: iDRAC hostname or IP.
        port: HTTPS port (default 443).
        verify: Whether to verify SSL certs.
        username: iDRAC username.
        password: iDRAC password.

    Returns:
        JSON string containing the attributes mapping.
    """
    if not host:
        logger.error("missing 'host' in params")
        raise ValueError("missing 'host' in params")

    url = _get_url(host=host, port=port)
    debug_log_params("get_idrac_attributes", {"host": host, "port": port, "verify": verify, "username": username, "password": password})

    client = DellRedfishClient(base_url=url, username=username, password=password, verify=verify)
    try:
        resp = client.get_idrac_attributes()
    except Exception:
        logger.exception("failed to fetch iDRAC attributes for %s", url)
        raise

    # Accept either a response-like object with `.dict` or a raw mapping
    data = getattr(resp, "dict", resp)
    return json.dumps(data, default=str)


@mcp.tool
def get_device_rollup_health_status(
    host: str,
    device_filter: str = "all",
    port: int = 443,
    verify: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """
    Get Dell rollup health status entries filtered by `device_filter`.

    Args:
        host: iDRAC hostname or IP.
        device_filter: "all" or comma-separated substrings to match against SubSystem.
        port: HTTPS port (default 443).
        verify: Whether to verify SSL certs.
        username: iDRAC username.
        password: iDRAC password.

    Returns:
        JSON string list of matched rollup members.
    """
    if not host:
        logger.error("missing 'host' in params")
        raise ValueError("missing 'host' in params")

    url = _get_url(host=host, port=port)
    debug_log_params("get_device_rollup_health_status", {"host": host, "port": port, "device_filter": device_filter, "verify": verify, "username": username, "password": password})

    client = DellRedfishClient(base_url=url, username=username, password=password, verify=verify)
    try:
        res = client.get_device_rollup_health_status(device_filter=device_filter)
    except Exception:
        logger.exception("failed to fetch rollup health status for %s", url)
        raise

    return json.dumps(res, default=str)


@mcp.tool
def get_memory_processor_health_information(
    host: str,
    device_name: str,
    port: int = 443,
    verify: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """
    For a given `device_name` collection, return mapping of member -> Health value.

    Args:
        host: iDRAC hostname or IP.
        device_name: The device collection name under `/Systems/...` (e.g. "Memory", "Processors").
    """
    if not host:
        logger.error("missing 'host' in params")
        raise ValueError("missing 'host' in params")
    if not device_name:
        logger.error("missing 'device_name' in params")
        raise ValueError("missing 'device_name' in params")

    url = _get_url(host=host, port=port)
    debug_log_params("get_memory_processor_health_information", {"host": host, "port": port, "device_name": device_name, "verify": verify, "username": username, "password": password})

    client = DellRedfishClient(base_url=url, username=username, password=password, verify=verify)
    try:
        res = client.get_memory_processor_health_information(device_name=device_name)
    except Exception:
        logger.exception("failed to fetch health information for %s %s", url, device_name)
        raise

    return json.dumps(res, default=str)


@mcp.tool
def get_server_slot_info(
    host: str,
    slot_type: Optional[str] = None,
    port: int = 443,
    verify: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """
    Retrieve Dell server slot information (DellSlots collection).

    Args:
        host: iDRAC hostname or IP.
        slot_type: Optional filter by ConnectorLayout (single or comma-separated).

    Returns:
        JSON list of slot member objects (possibly filtered).
    """
    if not host:
        logger.error("missing 'host' in params")
        raise ValueError("missing 'host' in params")

    url = _get_url(host=host, port=port)
    debug_log_params("get_server_slot_info", {"host": host, "port": port, "slot_type": slot_type, "verify": verify, "username": username, "password": password})

    client = DellRedfishClient(base_url=url, username=username, password=password, verify=verify)
    try:
        res = client.get_server_slot_info(slot_type=slot_type)
    except Exception:
        logger.exception("failed to fetch server slot info for %s", url)
        raise

    return json.dumps(res, default=str)


@mcp.tool
def export_server_screen_shot(
    host: str,
    filetype: int = 2,
    port: int = 443,
    verify: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Image:
    """
    Trigger a Dell LC action to export a server screenshot and return it as base64.

    Args:
        host: iDRAC hostname or IP.
        filetype: 0=LastCrashScreenShot, 1=Preview, 2=ServerScreenShot (default 2).

    Returns:
        ImageContent bytes
    """
    if not host:
        logger.error("missing 'host' in params")
        raise ValueError("missing 'host' in params")

    url = _get_url(host=host, port=port)
    debug_log_params("export_server_screen_shot", {"host": host, "port": port, "filetype": filetype, "verify": verify, "username": username, "password": password})

    client = DellRedfishClient(base_url=url, username=username, password=password, verify=verify)
    try:
        img_path = client.export_server_screen_shot(filetype=filetype)
    except Exception:
        logger.exception("failed to export server screenshot for %s", url)
        raise

    return Image(path=img_path, format="png")


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    return JSONResponse({"status": "ok"})

if __name__ == "__main__":
    # Run the MCP server over TCP so the container keeps running
    # and listens on port 8080 for incoming MCP connections.
    mcp.run(transport="http", host="0.0.0.0", port=8080)


__all__ = ["mcp"]
