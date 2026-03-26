("""Webhook handling service.

Exposes a `WebhookService` with a `handle_prometheus` method that accepts
Alertmanager/Prometheus webhook payloads and pushes simplified alert records
into a Redis stream via `RedisService`.
""")
from typing import Any, Dict, List
import logging

from .redis import RedisService

logger = logging.getLogger(__name__)


class WebhookService:
	def __init__(self, redis_service: RedisService):
		self.redis = redis_service

	def handle_prometheus(self, payload: Dict[str, Any], stream_name: str = "alerts") -> List[Dict[str, Any]]:
		"""Handle a Prometheus/Alertmanager webhook payload.

		Extracts `instance`, `id`, `message`, and `exported_severity` from each
		alert's `labels` (falling back to `annotations.description` for
		`message` if not present) and writes each extracted record to the
		configured Redis stream. Returns a list of results for each alert with
		either the created stream id or an error.
		"""
		results: List[Dict[str, Any]] = []
		alerts = payload.get("alerts") or []

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
				entry_id = self.redis.xadd(stream_name, record)
				results.append({"stream_id": entry_id, "record": record})
			except Exception as e:
				logger.exception("Failed to push alert to redis stream")
				results.append({"error": str(e), "record": record})

		return results


__all__ = ["WebhookService"]

