

```
pip install fastmcp pytest pytest-asyncio
pip install -e ./redfish-dell

docker compose -f tests/redfish-mockup-server/compose.yaml up -d

pytest -q -m integration tests/integration/test_fastmcp_server.py
```