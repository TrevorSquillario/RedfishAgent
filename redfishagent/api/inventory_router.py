
from fastapi import APIRouter, Request, Depends, HTTPException
from typing import Dict, Any, List

from utils.logging import setup_logger
from app import AgentFishApp
from utils.api import handle_api_exception

logger = setup_logger("api")

router = APIRouter(prefix="/api/inventory")


def get_app(request: Request) -> AgentFishApp:
	"""Dependency to retrieve the initialized AgentFishApp from app.state.

	Raises HTTPException(500) if the app hasn't been initialized.
	"""
	app_state = getattr(request.app, "state", None)
	redfishagent = getattr(app_state, "agentfish_app", None) if app_state is not None else None
	if redfishagent is None:
		raise HTTPException(status_code=500, detail="Server not initialized")
	return redfishagent


@router.get("/targets")
def get_targets(redfishagent: AgentFishApp = Depends(get_app)) -> List[Dict[str, Any]]:
	"""Run inventory plugins and return their results.

	Prefer the already-initialized `AgentFishApp.inventory_loader`. If that
	isn't available, fall back to a local `InventoryService` instance.
	"""
	try:
		logger.info("Handling /api/inventory/targets request")

		# Use pre-loaded inventory loader when available
		loader = getattr(redfishagent, "inventory_loader", None)
		if loader is None:
			return []

		return loader.run_inventory() or []

	except Exception as e:
		handle_api_exception(e)


