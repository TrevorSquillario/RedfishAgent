import sys
import json
from pathlib import Path

import pytest
import asyncio

from mcp_servers.prometheus import mcp_server

@pytest.fixture
async def main_mcp_client():
    async with Client(transport=mcp_server) as mcp_client:
        yield mcp_client

@pytest.fixture
def mcp_servers() -> list:
    return [
        "http://localhost:8092/mcp",
        "file:///home/trevor/git/RedfishAgent/redfishagent/mcp_servers/prometheus.py",
    ]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "redfish-mockup-14g",
        "redfish-mockup-15g",
        "redfish-mockup-16g",
        "redfish-mockup-17g",
    ],
)
async def test_discover_metrics(host, main_mcp_client):
    """Integration test for `discover_metrics` using the FastMCP client.

    Skips when `instance` not configured. Uses the in-process `mcp` transport
    to call the tool and asserts the returned data is a list.
    """
    if not host:
        pytest.skip("No HOST configured for integration test")

    result = await main_mcp_client.call_tool(
        name="discover_metrics",
        arguments={
            "host": host,
        },
    )

    assert result.data is not None
    data = json.loads(result.data)
    assert isinstance(data, list)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host,metric_name",
    [
        ("redfish-mockup-14g", "idrac_cpu_current_speed_mhz"),
        ("redfish-mockup-15g", "idrac_cpu_current_speed_mhz"),
        ("redfish-mockup-16g", "idrac_cpu_current_speed_mhz"),
        ("redfish-mockup-17g", "idrac_cpu_current_speed_mhz"),
         ("redfish-mockup-14g", "idrac_sensors_temperature"),
        ("redfish-mockup-15g", "idrac_sensors_temperature"),
        ("redfish-mockup-16g", "idrac_sensors_temperature"),
        ("redfish-mockup-17g", "idrac_sensors_temperature"),
    ],
)
async def test_get_metric_analysis(host, metric_name, main_mcp_client):
    """Integration test for `get_metric_analysis` using the FastMCP client.

    Skips when `instance` not configured. Uses the in-process `mcp` transport
    to call the tool and asserts the returned data contains an analysis string.
    """
    if not host:
        pytest.skip("No HOST configured for integration test")

    result = await main_mcp_client.call_tool(
        name="get_metric_analysis",
        arguments={
            "host": host,
            "metric_name": metric_name,
        },
    )

    assert result.data is not None
    data = json.loads(result.data)
    assert isinstance(data, dict)
    assert "analysis" in data
    assert isinstance(data["analysis"], str)
