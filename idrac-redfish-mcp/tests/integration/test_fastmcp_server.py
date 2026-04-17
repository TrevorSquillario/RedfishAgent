import sys
import os
import json
import base64
from pathlib import Path

import pytest

# Ensure `src` is importable
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from fastmcp_server import mcp

@pytest.mark.parametrize(
    "host, port",
    [
        ("localhost", 8001),
        ("localhost", 8002),
        ("localhost", 8003),
        ("localhost", 8004),
    ],
)
async def test_get_lc_logs(host, port, username, password, main_mcp_client):
	"""Integration test for `get_lc_logs` using the FastMCP Client.

	Skips when `HOST` not configured (matches other integration test
	behaviour). Uses the in-process `mcp` transport to call the tool
	and asserts the returned data is a list.
	"""
	if not host:
		pytest.skip('No HOST configured for integration test')

	result = await main_mcp_client.call_tool(
		name="get_lc_logs",
		arguments={
			"host": host,
            "port": port,
			"username": username,
			"password": password,
		},
	)

	assert result.data is not None
	data = json.loads(result.data)
	assert isinstance(data, list)


@pytest.mark.parametrize(
    "host, port",
    [
        ("localhost", 8001),
        ("localhost", 8002),
        ("localhost", 8003),
        ("localhost", 8004),
    ],
)
async def test_get_error_and_event_registry_cpu0001(host, port, username, password, main_mcp_client):
	"""Integration test for `get_error_and_event_registry` using local EEMI file.

	This test requests the `CPU0001` message id which is present in the
	bundled `files/eemi.json` and asserts the returned entry is a dict
	with expected `Severity` value.
	"""
	if not host:
		pytest.skip('No HOST configured for integration test')

	result = await main_mcp_client.call_tool(
		name="get_error_and_event_registry",
		arguments={
			"host": host,
			"port": port,
			"message_id": "CPU0001",
			"username": username,
			"password": password,
		},
	)

	assert result.data is not None
	data = json.loads(result.data)
	assert isinstance(data, dict)
	assert data.get("Severity") == "Critical"


@pytest.mark.parametrize(
	"host, port",
	[
		("localhost", 8001),
		("localhost", 8002),
		("localhost", 8003),
		("localhost", 8004),
	],
)
async def test_get_idrac_attributes(host, port, username, password, main_mcp_client):
	"""Integration test for `get_idrac_attributes`.

	Verifies the tool returns a mapping (dict-like) of attributes.
	"""
	if not host:
		pytest.skip('No HOST configured for integration test')

	result = await main_mcp_client.call_tool(
		name="get_idrac_attributes",
		arguments={
			"host": host,
			"port": port,
			"username": username,
			"password": password,
		},
	)

	assert result.data is not None
	data = json.loads(result.data)
	assert isinstance(data, (dict, list))


@pytest.mark.parametrize(
	"host, port",
	[
		("localhost", 8001),
		("localhost", 8002),
		("localhost", 8003),
		("localhost", 8004),
	],
)
async def test_get_device_rollup_health_status(host, port, username, password, main_mcp_client):
	"""Integration test for `get_device_rollup_health_status`.

	Ensures the tool returns a list of rollup members.
	"""
	if not host:
		pytest.skip('No HOST configured for integration test')

	result = await main_mcp_client.call_tool(
		name="get_device_rollup_health_status",
		arguments={
			"host": host,
			"port": port,
			"username": username,
			"password": password,
			"device_filter": "all",
		},
	)

	assert result.data is not None
	data = json.loads(result.data)
	assert isinstance(data, (list, dict))


@pytest.mark.parametrize(
	"host, port",
	[
		("localhost", 8001),
		("localhost", 8002),
		("localhost", 8003),
		("localhost", 8004),
	],
)
async def test_get_memory_processor_health_information(host, port, username, password, main_mcp_client):
	"""Integration test for `get_memory_processor_health_information`.

	Requests health for `Memory` collection and asserts mapping is returned.
	"""
	if not host:
		pytest.skip('No HOST configured for integration test')

	result = await main_mcp_client.call_tool(
		name="get_memory_processor_health_information",
		arguments={
			"host": host,
			"port": port,
			"username": username,
			"password": password,
			"device_name": "Memory",
		},
	)

	assert result.data is not None
	data = json.loads(result.data)
	assert isinstance(data, (dict, list))


@pytest.mark.parametrize(
	"host, port",
	[
		("localhost", 8001),
		("localhost", 8002),
		("localhost", 8003),
		("localhost", 8004),
	],
)
async def test_get_server_slot_info(host, port, username, password, main_mcp_client):
	"""Integration test for `get_server_slot_info`.

	Verifies slot info is returned.
	"""
	if not host:
		pytest.skip('No HOST configured for integration test')

	r1 = await main_mcp_client.call_tool(
		name="get_server_slot_info",
		arguments={
			"host": host,
			"port": port,
			"username": username,
			"password": password,
			"slot_type": None,
		},
	)
	assert r1.data is not None
	slots = json.loads(r1.data)
	assert isinstance(slots, (list, dict))

@pytest.mark.parametrize(
	"host, port",
	[
		("localhost", 8001),
		("localhost", 8002),
		("localhost", 8003),
		("localhost", 8004),
	],
)
async def test_export_server_screen_shot(host, port, username, password, main_mcp_client, tmp_path):
	"""Integration test for `export_server_screen_shot`.

	Verifies screenshot export returns a PNG image payload.
	"""
	if not host:
		pytest.skip('No HOST configured for integration test')

	r2 = await main_mcp_client.call_tool(
		name="export_server_screen_shot",
		arguments={
			"host": host,
			"port": port,
			"username": username,
			"password": password,
			"filetype": 0,
		},
	)
	# Expect the FastMCP call to return an ImageContent object in `content`.
	assert r2.content is not None and len(r2.content) > 0
	print(r2.content)
	img = r2.content[0]
	# `img.data` may be a base64 string or raw bytes
	b64 = img.data
	assert isinstance(b64, (bytes, str)) and b64
	raw = base64.b64decode(b64) if isinstance(b64, str) else b64
	# PNG files start with the signature: \x89PNG\r\n\x1a\n
	assert raw.startswith(b"\x89PNG\r\n\x1a\n")
	# prefer an explicit mimeType but accept a generic `image` type
	assert getattr(img, "mimeType", "").lower() == "image/png" or getattr(img, "type", "").lower() == "image"

