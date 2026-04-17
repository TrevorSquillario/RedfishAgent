import asyncio
import pytest
from types import SimpleNamespace

from graphs import redfish_agent
from services.llm import LLMService
from models.app import ConfigModel

@pytest.fixture
def user_prompt_default():
    return (
        "1. Use the get_error_and_event_registry tool to get details about the alerts.\n"
        "2. Get the top 10 lifecycle logs of severity Critical.\n"
        "3. Discover the related metrics using the discover_metrics tool, then query them using the get_metric_analysis tool."
    )


@pytest.fixture(autouse=True)
def set_test_envs(monkeypatch):
    """Set deterministic environment variables for integration tests."""
    monkeypatch.setenv("OPENAI_MODEL", "google/gemma-4-26B-A4B-it")
    monkeypatch.setenv("PROMETHEUS_URL", "http://localhost:8428")
    return

@pytest.mark.asyncio
async def test_main_runs_no_tools():
    # Provide a small injected state so the graph runs deterministically.
    inputs = {
        "messages": [("user", "Run a quick test.")],
        "event_id": "TEST-1",
        "message_id": "MSG-1",
        "message": "This is an integration test"
    }

    # Create a mock LogEntry similar to redfish_agent.main expectations
    log_entry = SimpleNamespace(
        source="test-host",
        labels={},
        event_id=inputs["event_id"],
        message_id=inputs["message_id"],
        message=inputs["message"],
    )

    # Minimal config object; redfish_agent only reads `prompts` if present
    config = ConfigModel(prompts={})

    # Use a longer timeout to avoid hanging CI if the live API is unavailable.
    await asyncio.wait_for(redfish_agent.main(log_entry, config, db_service=None, mcp_tools=[]), timeout=300)


@pytest.mark.asyncio
async def test_main_runs_with_tools_redfish_logs():
    # Provide a small injected state so the graph runs deterministically.
    inputs = {
        "messages": [("user", "Get the top 10 lifecycle logs of severity Critical for this host redfish-mockup-14g.")],
        "source": "redfish-mockup-14g",
        "event_id": "TEST-TOOLS-1",
        "message_id": "MSG-TOOLS-1",
        "message": "This is an integration test using MCP tools",
    }

    # Provide a simple ConfigModel so fetch_mcp_tools can discover tools
    cfg_model = ConfigModel(mcp_servers=[
        "http://localhost:8092/mcp",
    ])

    # Instantiate LLMService with the config and fetch MCP tools
    svc = LLMService(cfg_model)
    mcp_tools = await svc.fetch_mcp_tools()
    assert mcp_tools is not None

    # Create mock LogEntry and minimal config
    log_entry = SimpleNamespace(
        source=inputs.get("source"),
        labels={},
        event_id=inputs.get("event_id"),
        message_id=inputs.get("message_id"),
        message=inputs.get("message"),
    )
    config = ConfigModel(prompts={})

    # Use a longer timeout to avoid hanging CI if external services are unavailable.
    await asyncio.wait_for(
        redfish_agent.main(log_entry, config, db_service=None, mcp_tools=mcp_tools), timeout=300
    )

@pytest.mark.asyncio
async def test_main_runs_with_tools_metrics():
    # Provide a small injected state so the graph runs deterministically.
    inputs = {
        "messages": [("user", "Alert triggered host redfish-mockup-14g. Discover the related metrics using the discover_metrics tool, then query them using the get_metric_analysis tool.  CPU0001: CPU 1 has a thermal trip (over-temperature) event.")],
        "source": "redfish-mockup-14g",
        "event_id": "TEST-TOOLS-1",
        "message_id": "MSG-TOOLS-2",
        "message": "This is an integration test using MCP tools",
    }

    # Provide a simple ConfigModel so fetch_mcp_tools can discover tools
    cfg_model = ConfigModel(mcp_servers=[
        "http://localhost:8092/mcp",
        "file:///home/trevor/git/RedfishAgent/redfishagent/mcp_servers/prometheus.py",
    ])


    # Instantiate LLMService with the config and fetch MCP tools
    svc = LLMService(cfg_model)
    mcp_tools = await svc.fetch_mcp_tools()
    assert mcp_tools is not None

    # Create mock LogEntry and minimal config
    log_entry = SimpleNamespace(
        source=inputs.get("source"),
        labels={},
        event_id=inputs.get("event_id"),
        message_id=inputs.get("message_id"),
        message=inputs.get("message"),
    )
    config = ConfigModel(prompts={})

    # Use a longer timeout to avoid hanging CI if external services are unavailable.
    await asyncio.wait_for(
        redfish_agent.main(log_entry, config, db_service=None, mcp_tools=mcp_tools), timeout=300
    )

@pytest.mark.asyncio
async def test_main_runs_with_tools_combined(user_prompt_default):
    # Provide a small injected state so the graph runs deterministically.
    inputs = {
        "source": "redfish-mockup-14g",
        "event_id": "1001",
        "message_id": "CPU0001",
        "message": "This is an integration test using MCP tools",
        "labels": {
            "env": "test",
            "test": "test"
        }
    }
    # envs are set by autouse fixture `set_test_envs`

    # Provide a ConfigModel and fetch MCP tools via LLMService instance
    cfg_model = ConfigModel(
        mcp_servers=[
            "http://localhost:8092/mcp",
            "file:///home/trevor/git/RedfishAgent/redfishagent/mcp_servers/prometheus.py",
        ],
        prompts={
            "redfish_agent": {"main": user_prompt_default}
        },
    )
    svc = LLMService(cfg_model)
    mcp_tools = await svc.fetch_mcp_tools()
    assert mcp_tools is not None

    # Create mock LogEntry and minimal config
    log_entry = SimpleNamespace(
        source=inputs.get("source"),
        labels={},
        event_id=inputs.get("event_id"),
        message_id=inputs.get("message_id"),
        message=inputs.get("message"),
    )

    # Use a longer timeout to avoid hanging CI if external services are unavailable.
    await asyncio.wait_for(
        redfish_agent.main(log_entry, cfg_model, db_service=None, mcp_tools=mcp_tools), timeout=300
    )

@pytest.mark.asyncio
async def test_main_runs_with_tools_screenshot(user_prompt_default):
    user_prompt = """
        Use the export_server_screen_shot tool to provide a summary of the Preview image for redfish-mockup-14g <|image|>
    """
    # Provide a small injected state so the graph runs deterministically.
    inputs = {
        "source": "redfish-mockup-14g",
        "event_id": "1001",     
        "message_id": "CPU0001",
        "message": "This is an integration test using MCP tools",
        "labels": {
            "env": "test",
            "test": "test"
        }
    }
    # envs are set by autouse fixture `set_test_envs`

    # Provide a ConfigModel and fetch MCP tools via LLMService instance
    cfg_model = ConfigModel(
        mcp_servers=[
            "http://localhost:8092/mcp",
        ],
        prompts={
            "redfish_agent": {"main": user_prompt}
        },
    )
    svc = LLMService(cfg_model)
    mcp_tools = await svc.fetch_mcp_tools()
    assert mcp_tools is not None

    # Create mock LogEntry and minimal config
    log_entry = SimpleNamespace(
        source=inputs.get("source"),
        labels={},
        event_id=inputs.get("event_id"),
        message_id=inputs.get("message_id"),
        message=inputs.get("message"),
    )

    # Use a longer timeout to avoid hanging CI if external services are unavailable.
    await asyncio.wait_for(
        redfish_agent.main(log_entry, cfg_model, db_service=None, mcp_tools=mcp_tools), timeout=300
    )