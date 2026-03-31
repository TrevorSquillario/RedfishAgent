
from fastapi import APIRouter, Request, Depends, HTTPException
from typing import Dict, Any, List

from utils.logging import setup_logger
from app import RedfishAgentApp
from utils.api import handle_api_exception
from services.inventory import InventoryService

logger = setup_logger("api")

router = APIRouter(prefix="/api/inventory")


def get_app(request: Request) -> RedfishAgentApp:
	"""Dependency to retrieve the initialized RedfishAgentApp from app.state.

	Raises HTTPException(500) if the app hasn't been initialized.
	"""
	app_state = getattr(request.app, "state", None)
	redfishagent = getattr(app_state, "redfishagent_app", None) if app_state is not None else None
	if redfishagent is None:
		raise HTTPException(status_code=500, detail="Server not initialized")
	return redfishagent


@router.get("/targets")
def get_targets(redfishagent: RedfishAgentApp = Depends(get_app)) -> List[Dict[str, Any]]:
	"""Run inventory plugins and return their results.

	Prefer the already-initialized `RedfishAgentApp.inventory_loader`. If that
	isn't available, fall back to a local `InventoryService` instance.
	"""
	try:
		logger.info("Handling /api/inventory/targets request")

		# Prefer the shared InventoryService when available
		service = getattr(redfishagent, "inventory_service", None)
		if service is not None:
			return service.run_inventory() or []

		# Fallback to the raw loader if service unavailable
		loader = getattr(redfishagent, "inventory_loader", None)
		if loader is None:
			return []

		return loader.run_inventory() or []

	except Exception as e:
		handle_api_exception(e)


@router.get("/list")
def get_list(redfishagent: RedfishAgentApp = Depends(get_app)) -> List[Dict[str, Any]]:
	"""Return inventory plugin results without adding the exporter URL."""
	try:
		logger.info("Handling /api/inventory/list request")

		# Use the app-level InventoryService when available
		service = getattr(redfishagent, "inventory_service", None)
		if service is not None:
			return service.run_inventory(add_exporter_url=False) or []

		# Fall back: create a temporary InventoryService bound to the app loader
		loader = getattr(redfishagent, "inventory_loader", None)
		if loader is not None:
			tmp = InventoryService(plugin_loader=loader)
			return tmp.run_inventory(add_exporter_url=False) or []

		return []

	except Exception as e:
		handle_api_exception(e)


