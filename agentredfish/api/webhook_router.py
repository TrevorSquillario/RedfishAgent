
import json
from fastapi import APIRouter, Request, Depends, HTTPException
from typing import Dict, Any, List

from utils.logging import setup_logger
from app import AgentFishApp
from utils.api import handle_api_exception
logger = setup_logger("api")

router = APIRouter(prefix="/api/webhook")


def get_app(request: Request) -> AgentFishApp:
	"""Dependency to retrieve the initialized AgentFishApp from app.state.

	Raises HTTPException(500) if the app hasn't been initialized.
	"""
	app_state = getattr(request.app, "state", None)
	agentredfish = getattr(app_state, "agentfish_app", None) if app_state is not None else None
	if agentredfish is None:
		raise HTTPException(status_code=500, detail="Server not initialized")
	return agentredfish


@router.post("/alerts/prometheus")
async def receive_alerts(request: Request, agentredfish: AgentFishApp = Depends(get_app)) -> Dict[str, Any]:
	"""Receive Alertmanager webhook POSTs at /api/webhook/alerts.

	Stores the last-received payload on the `AgentFishApp` instance
	(at `last_webhook_alert`) and logs it. Returns a small ack.
	"""
	try:
		logger.info("Handling /api/webhook/alerts request")
		payload = await request.json()
		#logger.debug(f"Webhook payload: {payload}")
		logger.info(f"{len(payload.get('alerts', []))} alerts received")

		# Persist payload on the app instance for other services/plugins
		# Use the webhook service initialized on the AgentFishApp instance
		wh = getattr(agentredfish, "webhook_service", None)
		if wh is not None:
			wh.handle_prometheus(payload)
		else:
			logger.warning("No webhook_service available on AgentFishApp")

		return {"status": "received"}

	except Exception as e:
		handle_api_exception(e)


