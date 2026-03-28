import os

import sys
from pathlib import Path

import pytest

# Ensure `src` is importable
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from fastmcp.client import Client
from fastmcp_server import mcp

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
    async with Client(transport=mcp) as mcp_client:
        yield mcp_client
