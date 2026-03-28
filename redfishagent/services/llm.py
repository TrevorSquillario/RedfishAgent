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
	def __init__(self, redis_service: RedisService, db_service: Optional[Any] = None, config_service: Optional[Any] = None):
		self.redis = redis_service
		self.db = db_service
		self.config = config_service
		self._running = False

	@staticmethod
	async def fetch_mcp_tools(config_service: Optional[Any] = None) -> Optional[Any]:
		"""Discover MCP servers and return the currently-available tools.

		This helper mirrors the MCP lookup logic from the graph and returns
		the result of `MultiServerMCPClient.get_tools()` or `None` on error.
		"""
		# Local import to avoid import cycles at module import time
		from langchain_mcp_adapters.client import MultiServerMCPClient
		from graphs.utils.mcp import build_mcp_servers_map

		# Determine mcp entries from a variety of possible config shapes
		mcp_entries = None
		if config_service is not None:
			if isinstance(config_service, dict):
				mcp_entries = config_service.get("mcp")
			elif hasattr(config_service, "get"):
				try:
					mcp_entries = config_service.get("mcp")
				except Exception:
					mcp_entries = None
			elif hasattr(config_service, "mcp"):
				mcp_entries = getattr(config_service, "mcp")
		else:
			logger.error(f"Config Service not provided")

		servers_map = build_mcp_servers_map(mcp_entries)

		try:
			logger.info("Discovered MCP servers: %s", list(servers_map.keys()))
		except Exception:
			logger.exception("Failed logging MCP servers list")

		try:
			mcp_client = MultiServerMCPClient(servers_map)
			mcp_tools = await mcp_client.get_tools()

			# Log discovered tools for visibility
			try:
				tool_names = None
				if isinstance(mcp_tools, dict):
					tool_names = list(mcp_tools.keys())
				elif isinstance(mcp_tools, (list, tuple)):
					names = []
					for t in mcp_tools:
						if isinstance(t, dict) and "name" in t:
							names.append(t["name"])
						elif hasattr(t, "name"):
							names.append(getattr(t, "name"))
						else:
							names.append(str(t))
					tool_names = names
				else:
					tool_names = [str(mcp_tools)]

				logger.info("Discovered MCP tools: %s", tool_names)
			except Exception:
				logger.exception("Failed logging MCP tools list")

			return mcp_tools
		except Exception:
			logger.exception("Failed to fetch MCP tools")
			return None

	def subscribe_to_alerts(self, stream_name: Optional[str] = None, start_id: str = "0-0", block_ms: int = 1000) -> None:
		"""Listen to the configured Redis stream and log incoming entries.

		If `stream_name` is not provided, the environment variable
		`REDIS_STREAM` is used. If that is not set, the default
		`redfish_events` is used to match the Go ingestor output.

		This method blocks; call it in a background thread/process if you want
		non-blocking behaviour. It uses the underlying Redis client via
		`RedisService._client` to perform `xread` calls.
		"""
		# determine stream name: prefer explicit arg, then env REDIS_STREAM,
		# then fall back to the hardcoded default used by the Go ingestor.
		stream_name = stream_name or os.getenv("REDIS_STREAM", "redfish_events")

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
		api_base = os.getenv("OPENAI_API_BASE_EMBED", os.getenv("OPENAI_API_BASE"))
		api_key = os.getenv("OPENAI_API_KEY_EMBED", os.getenv("OPENAI_API_KEY"))
		model = os.getenv("OPENAI_MODEL_EMBED", "Qwen3-Embedding-4B")

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

	async def run_agent(self, config_service: Optional[Any] = None) -> None:
		"""Run the agent graph defined in `redfishagent.graphs.default`.

		This wraps the graph's `main` runner. It passes this service's `db`
		as the `db_service` argument so the graph can access the database if
		needed. Any exceptions during import or execution are logged and the
		method returns gracefully.
		"""
		try:
			from graphs import redfish_agent
		except Exception:
			logger.exception("Failed to import agent graph from redfishagent.graphs.default")
			return

		try:
			# Prefer the explicitly-passed config_service, otherwise use the
			# config that may have been provided at construction time. Pass
			# through any pre-fetched `mcp_tools` so the graph doesn't have to
			# rediscover them itself.
			cfg = config_service or self.config
			try:
				mcp_tools = await LLMService.fetch_mcp_tools(cfg)
			except Exception:
				logger.exception("Failed to fetch MCP tools before running agent")

			await redfish_agent.main(db_service=self.db, config_service=cfg, mcp_tools=mcp_tools)
		except Exception:
			logger.exception("Error while running agent graph")


__all__ = ["LLMService"]

