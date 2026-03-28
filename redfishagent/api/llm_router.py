import json
from fastapi import APIRouter, Request, Depends, HTTPException
from typing import Dict, Any

from utils.logging import setup_logger
from app import RedfishAgentApp
from utils.api import handle_api_exception
from services.llm import LLMService
logger = setup_logger("api")

router = APIRouter(prefix="/api/llm")


def get_app(request: Request) -> RedfishAgentApp:
	"""Dependency to retrieve the initialized RedfishAgentApp from app.state.

	Raises HTTPException(500) if the app hasn't been initialized.
	"""
	app_state = getattr(request.app, "state", None)
	redfishagent = getattr(app_state, "redfishagent_app", None) if app_state is not None else None
	if redfishagent is None:
		raise HTTPException(status_code=500, detail="Server not initialized")
	return redfishagent


@router.get("/test")
async def test_llm(redfishagent: RedfishAgentApp = Depends(get_app)) -> Dict[str, Any]:
	"""Endpoint to trigger the LLM agent run.

	Calls `await self.llm_service.run_agent()` on the application `RedfishAgentApp`.
	"""
	try:
		logger.info("Handling /api/llm/test request")

		llm = getattr(redfishagent, "llm_service", None)
		if llm is None:
			logger.warning("No llm_service available on RedfishAgentApp")
			raise HTTPException(status_code=500, detail="LLM service not available")

		result = await llm.run_agent()

		return {"status": "ok", "result": result}

	except Exception as e:
		handle_api_exception(e)


@router.get("/tools")
async def get_mcp_tools(redfishagent: RedfishAgentApp = Depends(get_app)) -> Dict[str, Any]:
	"""Return the list of MCP tools discovered via `LLMService.fetch_mcp_tools`.

	This calls the same helper used by the service to discover tools so
	callers can inspect available tools without running the agent.
	"""
	try:
		logger.info("Handling /api/llm/tools request")

		# Prefer using the app-config available on the RedfishAgentApp
		cfg = getattr(redfishagent, "config_service", None)

		tools = await LLMService.fetch_mcp_tools(cfg)

		if tools is None:
			raise HTTPException(status_code=500, detail="Failed to fetch MCP tools")

		return {"status": "ok", "tools": tools}

	except Exception as e:
		handle_api_exception(e)

