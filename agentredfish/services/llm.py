"""LLM-related services.

Provides a lightweight `LLMService` that can subscribe to the `alerts`
Redis stream and log incoming entries for now.
"""
from typing import Any, Dict, Optional
import logging
import time
import os

import openai

from .redis import RedisService

logger = logging.getLogger(__name__)


class LLMService:
	def __init__(self, redis_service: RedisService):
		self.redis = redis_service
		self._running = False

	def subscribe_to_alerts(self, stream_name: str = "alerts", start_id: str = "0-0", block_ms: int = 1000) -> None:
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
		"""Stop the subscribe loop started by `subscribe_to_alerts`."""
		self._running = False

	def create_embedding(self, text: str) -> Optional[list[float]]:
		"""Create an embedding for `text` using an OpenAI-compatible API.

		Reads these environment variables (defaults used if not set):
		- `OPENAI_API_BASE` : base URL for a local OpenAI-compatible server
		- `OPENAI_API_KEY`  : API key (optional)
		- `OPENAI_MODEL`    : model name (defaults to `text-embedding-3-small`)

		Returns the embedding vector on success or `None` on error.
		"""
		api_base = os.getenv("OPENAI_API_BASE")
		api_key = os.getenv("OPENAI_API_KEY")
		model = os.getenv("OPENAI_MODEL", "text-embedding-3-small")

		if api_key:
			openai.api_key = api_key
		if api_base:
			openai.api_base = api_base

		try:
			resp = openai.Embedding.create(input=text, model=model)
			embedding = resp["data"][0]["embedding"]
			return embedding
		except Exception:
			logger.exception("Error creating embedding via OpenAI-compatible API")
			return None


__all__ = ["LLMService"]

