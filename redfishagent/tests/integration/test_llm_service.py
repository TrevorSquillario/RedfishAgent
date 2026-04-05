import sys
import types
import pytest
from pathlib import Path
from typing import Any, Dict, Optional, List

from services.llm import LLMService
from services.config import ConfigService

@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_mcp_tools_with_mocked_services(mcp_servers):
    config_service = object()
    mock_redis = object()
    mock_db = object()

    svc = LLMService(redis_service=mock_redis, db_service=mock_db, config_service=config_service)

    # Call the static async helper and verify the returned tools
    result = await svc.fetch_mcp_tools(config_service=config_service, mcp_servers=mcp_servers)
    assert result is not None
    assert len(result) > 0



