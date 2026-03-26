("""Redis helper service.

Provides a thin wrapper around `redis.Redis` for adding stream entries.
""")
from typing import Any, Dict, Optional
import json
import logging

import redis

logger = logging.getLogger(__name__)


class RedisService:
	def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0, password: Optional[str] = None, decode_responses: bool = True):
		self._client = redis.Redis(host=host, port=port, db=db, password=password, decode_responses=decode_responses)

	def xadd(self, stream: str, fields: Dict[str, Any], maxlen: Optional[int] = None) -> str:
		"""Add an entry to a Redis stream.

		- Converts values to strings (JSON for dict/list).
		- If `maxlen` is provided it will use approximate trimming.

		Returns the entry id created by Redis.
		"""
		# Convert values to strings suitable for Redis
		prepared: Dict[str, str] = {}
		for k, v in fields.items():
			if v is None:
				prepared[k] = ""
			elif isinstance(v, (dict, list)):
				prepared[k] = json.dumps(v)
			else:
				prepared[k] = str(v)

		try:
			if maxlen:
				return self._client.xadd(stream, prepared, maxlen=maxlen, approximate=True)
			return self._client.xadd(stream, prepared)
		except Exception:
			logger.exception("Failed to add entry to Redis stream %s", stream)
			raise


__all__ = ["RedisService"]

