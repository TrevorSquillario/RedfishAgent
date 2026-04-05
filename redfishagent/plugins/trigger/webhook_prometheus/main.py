"""Prometheus/Alertmanager webhook trigger plugin.

Registers a POST /api/webhook/alerts/prometheus route on the main FastAPI app
during ``initialize``, replicating the behaviour previously in
``api/webhook_router.py``.  The endpoint delegates to the ``WebhookService``
already attached to ``RedfishAgentApp`` (``app.webhook_service``).

The ``run`` coroutine is a no-op idle loop: all work happens inside the
FastAPI request handler which is driven by uvicorn, not by this loop.
"""
from typing import Any, Dict, Optional, List
import asyncio
import logging
import os
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import redis as redis_lib

from base.trigger_plugin_base import TriggerPluginInterface
from utils.logging import setup_logger

logger = setup_logger(__name__)


class WebhookPrometheusTriggerPlugin(TriggerPluginInterface):
    plugin_type = "trigger"

    def __init__(self) -> None:
        self._running: bool = False
        self._app_obj: Optional[Any] = None  # RedfishAgentApp
        self._redis_client: Optional[Any] = None
        self._stream_name: str = "alerts"

    # ------------------------------------------------------------------
    # TriggerPluginInterface
    # ------------------------------------------------------------------

    def initialize(self, context: Dict[str, Any]) -> None:
        """Register the webhook route on the FastAPI app.

        Required context keys:
        - ``fastapi_app``: the FastAPI application instance.
        - ``app``: the ``RedfishAgentApp`` instance (for ``webhook_service``).
        """
        fastapi_app = context.get("fastapi_app")
        self._app_obj = context.get("app")

        if fastapi_app is None:
            raise RuntimeError(
                "WebhookPrometheusTriggerPlugin requires 'fastapi_app' in context. "
                "Ensure the FastAPI app is created before RedfishAgentApp is initialized."
            )

        # Prepare redis client (allow injection via context)
        self._redis_client = context.get("redis_client")
        if self._redis_client is None:
            host = os.getenv("REDIS_HOST", "redis")
            port = int(os.getenv("REDIS_PORT", "6379"))
            db = int(os.getenv("REDIS_DB", "0"))
            password = os.getenv("REDIS_PASSWORD") or None
            try:
                self._redis_client = redis_lib.Redis(host=host, port=port, db=db, password=password, decode_responses=True)
            except Exception:
                logger.exception("webhook_prometheus: failed to create redis client")

        # Expose this plugin instance on the app so other code can call it
        try:
            if self._app_obj is not None:
                setattr(self._app_obj, "webhook_service", self)
        except Exception:
            logger.exception("webhook_prometheus: failed to attach to RedfishAgentApp")

        router = APIRouter(prefix="/api/webhook")

        @router.post("/alerts/prometheus")
        async def receive_alerts(request: Request) -> Dict[str, Any]:
            """Accept Alertmanager/Prometheus webhook POSTs and push to Redis."""
            try:
                payload = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON payload")

            alert_count = len(payload.get("alerts", []))
            logger.info("webhook_prometheus: %d alert(s) received", alert_count)

            # Delegate to this plugin's handler (also available as app.webhook_service)
            try:
                results = self.handle_prometheus(payload)
            except Exception:
                logger.exception("webhook_prometheus: handle_prometheus raised")
                raise HTTPException(status_code=500, detail="Internal server error")

            return {"status": "received", "processed": len(results)}

    def handle_prometheus(self, payload: Dict[str, Any], stream_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Process an Alertmanager webhook payload and push simplified records to Redis.

        Returns a list of dicts describing the push results (stream_id or error).
        """
        results: List[Dict[str, Any]] = []
        alerts = payload.get("alerts") or []

        sn = stream_name or self._stream_name

        for alert in alerts:
            labels = alert.get("labels", {}) or {}
            annotations = alert.get("annotations", {}) or {}

            instance = labels.get("instance")
            alert_id = labels.get("id")
            message = labels.get("message") or annotations.get("description")
            exported_severity = labels.get("exported_severity")

            record = {
                "instance": instance or "",
                "id": alert_id or "",
                "message": message or "",
                "exported_severity": exported_severity or "",
                "fingerprint": alert.get("fingerprint", ""),
                "startsAt": alert.get("startsAt", ""),
            }

            try:
                prepared: Dict[str, str] = {
                    k: (json.dumps(v) if isinstance(v, (dict, list)) else ("" if v is None else str(v)))
                    for k, v in record.items()
                }
                entry_id = self._redis_client.xadd(sn, prepared)
                results.append({"stream_id": entry_id, "record": record})
            except Exception as e:
                logger.exception("webhook_prometheus: Failed to push alert to redis stream")
                results.append({"error": str(e), "record": record})

        return results

        fastapi_app.include_router(router)
        logger.info("WebhookPrometheusTriggerPlugin: registered POST /api/webhook/alerts/prometheus")

    async def run(self) -> None:
        """Idle loop — the FastAPI endpoint is driven by uvicorn, not this loop."""
        self._running = True
        logger.info("WebhookPrometheusTriggerPlugin: running (endpoint active)")
        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            self._running = False
            logger.info("WebhookPrometheusTriggerPlugin: stopped")

    def stop(self) -> None:
        self._running = False
