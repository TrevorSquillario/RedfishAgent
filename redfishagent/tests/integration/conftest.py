import os

import sys
from pathlib import Path

import pytest
import asyncio

# Ensure repo root is on sys.path so `redfishagent` package is importable
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from redfishagent.mcp_servers.prometheus import mcp_server
from fastmcp.client import Client

@pytest.fixture(scope='session')
def username():
    """Test fixture for username (from env `USERNAME`)."""
    return os.getenv('USERNAME', None)


@pytest.fixture(scope='session')
def password():
    """Test fixture for password (from env `PASSWORD`)."""
    return os.getenv('PASSWORD', None)

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
