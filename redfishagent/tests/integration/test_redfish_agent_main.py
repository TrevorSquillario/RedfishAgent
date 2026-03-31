import asyncio
import pytest

from redfishagent.graphs import redfish_agent
from redfishagent.services.llm import LLMService

@pytest.mark.integration
@pytest.mark.asyncio
async def test_main_runs_no_tools():
    # Provide a small injected state so the graph runs deterministically.
    inputs = {
        "messages": [("user", "Run a quick test.")],
        "alert_id": "TEST-1",
        "query_message": "This is an integration test"
    }

    # Use a short timeout to avoid hanging CI if the live API is unavailable.
    await asyncio.wait_for(redfish_agent.main(db_service=None, mcp_tools={}, inputs=inputs), timeout=60)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_main_runs_with_tools_redfish_logs():
    # Provide a small injected state so the graph runs deterministically.
    inputs = {
        "messages": [("user", "Get the top 10 lifecycle logs of severity Critical for this host redfish-mockup-14g.")],
        "source": "redfish-mockup-14g",
        "alert_id": "TEST-TOOLS-1",
        "query_message": "This is an integration test using MCP tools",
    }

    # Provide a simple config shape with MCP entries so fetch_mcp_tools can discover tools
    cfg = {
        "mcp": [
            "http://localhost:8092/mcp",
            "file:///home/trevor/git/RedfishAgent/redfishagent/mcp_servers/prometheus.py",
        ]
    }

    # Fetch MCP tools and pass them to the real graph
    mcp_tools = await LLMService.fetch_mcp_tools(cfg)
    assert mcp_tools is not None

    # Use a short timeout to avoid hanging CI if external services are unavailable.
    await asyncio.wait_for(
        redfish_agent.main(db_service=None, mcp_tools=mcp_tools, inputs=inputs), timeout=120
    )

@pytest.mark.integration
@pytest.mark.asyncio
async def test_main_runs_with_tools_metrics():
    # Provide a small injected state so the graph runs deterministically.
    inputs = {
        "messages": [("user", "Alert triggered host redfish-mockup-14g. Discover the related metrics using the discover_metrics tool, then query them using the get_metric_analysis tool.  CPU0001: CPU 1 has a thermal trip (over-temperature) event.")],
        "source": "redfish-mockup-14g",
        "alert_id": "TEST-TOOLS-1",
        "query_message": "This is an integration test using MCP tools",
    }

    # Provide a simple config shape with MCP entries so fetch_mcp_tools can discover tools
    cfg = {
        "mcp": [
            "http://localhost:8092/mcp",
            "file:///home/trevor/git/RedfishAgent/redfishagent/mcp_servers/prometheus.py",
        ]
    }

    # Fetch MCP tools and pass them to the real graph
    mcp_tools = await LLMService.fetch_mcp_tools(cfg)
    assert mcp_tools is not None

    # Use a short timeout to avoid hanging CI if external services are unavailable.
    await asyncio.wait_for(
        redfish_agent.main(db_service=None, mcp_tools=mcp_tools, inputs=inputs), timeout=120
    )