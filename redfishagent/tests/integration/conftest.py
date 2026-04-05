import os

import sys
from pathlib import Path

import pytest
import asyncio

# Ensure repo root is on sys.path so `redfishagent` package is importable
ROOT = Path(__file__).resolve().parents[3]   # repo root (existing)
sys.path.insert(0, str(ROOT / "redfishagent"))   # add package dir so `import utils` works

from fastmcp.client import Client

@pytest.fixture(scope='session')
def username():
    """Test fixture for username (from env `USERNAME`)."""
    return os.getenv('USERNAME', None)


@pytest.fixture(scope='session')
def password():
    """Test fixture for password (from env `PASSWORD`)."""
    return os.getenv('PASSWORD', None)

