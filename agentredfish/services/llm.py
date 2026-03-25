"""LLM-related services.

Provides a lightweight `LLMService` that can subscribe to the `alerts`
Redis stream and log incoming entries for now.
"""
from typing import Any, Dict, Optional
import logging
import time

from .redis import RedisService

logger = logging.getLogger(__name__)


class LLMService:
	def __init__(self, redis_service: RedisService):
		self.redis = redis_service
		self._running = False

	def subscribeToAlerts(self, stream_name: str = "alerts", start_id: str = "0-0", block_ms: int = 1000) -> None:
		"""Listen to the configured Redis stream and log incoming entries.

		This method blocks; call it in a background thread/process if you want
		non-blocking behaviour. It uses the underlying Redis client via
		`RedisService._client` to perform `xread` calls.
		"""
		last_id = start_id
		client = getattr(self.redis, "_client", None)
		if client is None:
			logger.error("Redis client not available on RedisService")
			return

		self._running = True
		try:
			while self._running:
				try:
					results = client.xread({stream_name: last_id}, block=block_ms, count=10)
					if not results:
						continue
					for stream_name, entries in results:
						for entry_id, fields in entries:
							logger.info("Alert stream entry %s -> %s", entry_id, fields)
							last_id = entry_id
				except Exception:
					logger.exception("Error reading from Redis stream %s", stream_name)
					time.sleep(1)
		finally:
			self._running = False

	def stop(self) -> None:
		"""Stop the subscribe loop started by `subscribeToAlerts`."""
		self._running = False


__all__ = ["LLMService"]

