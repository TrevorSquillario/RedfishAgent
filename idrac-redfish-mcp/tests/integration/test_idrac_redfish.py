import os
import sys
from pathlib import Path

import pytest
import requests

# Ensure `src` is importable
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from utils.idrac_redfish import iDRACRedfish

def test_login_session_pass():
    """Integration test against a real Redfish server.

    Requires environment variables: `HOST`, `USERNAME`, `PASSWORD`.
    Optionally set `BASE_URL` and `VERIFY` (0/1).
    """

    host = os.getenv('HOST', 'localhost')
    port = os.getenv('PORT', 8001)
    username = os.getenv('USERNAME', 'root')
    password = os.getenv('PASSWORD', 'calvin')

    if not host:
        pytest.skip('No HOST configured for integration test')

    client = iDRACRedfish(host, port=port, verify=False, timeout=10)

    # Basic API root check
    r = client.get('/')
    assert r.status_code == 200
    assert isinstance(r.json(), dict)

    # Try session login if credentials provided; skip if server doesn't support sessions
    if username and password:
        client.login(username, password)
        assert client.token_valid(), 'token not present after login'
