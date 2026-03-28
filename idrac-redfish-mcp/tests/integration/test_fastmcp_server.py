import sys
import json
from pathlib import Path

import pytest

# Ensure `src` is importable
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from fastmcp_server import mcp

@pytest.mark.integration
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

