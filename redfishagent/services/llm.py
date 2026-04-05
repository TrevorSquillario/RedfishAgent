"""LLM-related services.

Provides a lightweight `LLMService` that can subscribe to the `alerts`
Redis stream and log incoming entries for now.
"""
from typing import Any, Dict, Optional, List
import logging
import time
import os
import re
import json
from urllib.parse import urlparse, parse_qs

import openai

from models.app import RedfishLogEntry, ConfigModel, PluginsConfig
from .redis import RedisService
from .inventory import InventoryService

logger = logging.getLogger(__name__)

class LLMService:
	def __init__(self, redis_service: RedisService, config: ConfigModel, db_service: Optional[Any] = None, inventory_service: Optional[InventoryService] = None, output_service: Optional[Any] = None):
		self.redis = redis_service
		self.db = db_service
		self.config = config
		self._running = False
		self.inventory = inventory_service
		self.output = output_service

	async def fetch_mcp_tools(self) -> Optional[Any]:
		"""Discover MCP servers and return the currently-available tools.

		This helper mirrors the MCP lookup logic from the graph and returns
		the result of `MultiServerMCPClient.get_tools()` or `None` on error.
		"""
		# Local import to avoid import cycles at module import time
		from langchain_mcp_adapters.client import MultiServerMCPClient
		from graphs.utils.mcp import build_mcp_servers_map

		mcp_entries = self.config.mcp_servers

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

	async def subscribe_to_alerts(self, stream_name: Optional[str] = None, start_id: str = "0-0", block_ms: int = 1000) -> None: 
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
							# Extract payload and source from the stream entry; handle bytes keys/values
							def _get_field(key: str):
								if key in fields:
									return fields[key]
								bkey = key.encode()
								if bkey in fields:
									return fields[bkey]
								return None

							raw_payload = _get_field("payload")
							raw_source = _get_field("source")

							# Normalize to strings
							try:
								payload_str = raw_payload.decode("utf-8") if isinstance(raw_payload, (bytes, bytearray)) else str(raw_payload)
							except Exception:
								payload_str = ""
							try:
								source = raw_source.decode("utf-8") if isinstance(raw_source, (bytes, bytearray)) else str(raw_source)
							except Exception:
								source = ""

							# Parse payload JSON when possible
							payload_obj: Dict[str, Any]
							try:
								payload_obj = json.loads(payload_str) if payload_str else {}
							except Exception:
								logger.exception("Failed to parse payload JSON for entry %s", entry_id)
								payload_obj = {"raw": payload_str}

							# Lookup labels from inventory for this source
							labels: Dict[str, Any] = {}
							try:
								inv = self.inventory.get_inventory()
								logger.debug(f"Inventory: {inv}")
								# Find matching inventory entry by comparing source against targets
								for inv_entry in inv:
									targets = inv_entry.get("targets", []) or []
									for t in targets:
										ts = str(t)
										match_name = ts.split(":")[0]
										logger.debug(f"Searching for labels on {match_name}")
										if match_name and match_name == source:
											labels = inv_entry.get("labels", {}) or {}
											logger.debug(f"Labels found: {labels}")
											break
									if labels:
										break
							except Exception:
								logger.exception("Failed looking up inventory labels for source %s", source)

							entry_obj = RedfishLogEntry(source=source, payload=payload_obj, labels=labels)
							logger.info("RedfishLogEntry %s -> %s", entry_id, entry_obj)
							last_id = entry_id
							try:
								await self.run_agent(log_entry=entry_obj)
								# Delete the processed message from the Redis stream
								try:
									client.xdel(stream_name, entry_id)
									logger.debug("Deleted stream entry %s from %s", entry_id, stream_name)
								except Exception:
									logger.exception("Failed to delete stream entry %s from %s", entry_id, stream_name)
							except Exception:
								logger.exception("Error while running agent for entry %s", entry_id)
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

	async def run_agent(self, log_entry: Optional[RedfishLogEntry] = None) -> None:
		"""Run the agent graph defined in `graphs.default`.

		This wraps the graph's `main` runner. It passes this service's `db`
		as the `db_service` argument so the graph can access the database if
		needed. If provided, `log_entry` is forwarded to the graph as
		`log_entry` so the agent can act on the current log entry. Any
		exceptions during import or execution are logged and the method
		returns gracefully.
		"""
		try:
			from graphs import redfish_agent
		except Exception:
			logger.exception("Failed to import agent graph from graphs.default")
			return

		try:
			try:
				mcp_tools = await self.fetch_mcp_tools()
			except Exception:
				logger.exception("Failed to fetch MCP tools before running agent")

			final_state = await redfish_agent.main(db_service=self.db, mcp_tools=mcp_tools, config=self.config, log_entry=log_entry)

			logger.debug(f"redfish_agent final state: {final_state}")
			# If an OutputService was injected, send the final message text to it.
			try:
				if getattr(self, "output", None) is not None:
					try:
						output_text = final_state.get("final_message", "") if isinstance(final_state, dict) else ""
						self.output.run_output(output_text)
						logger.info("Sent final agent message to OutputService")
					except Exception:
						logger.exception("Failed sending agent output to OutputService")
			except Exception:
				logger.exception("Unexpected error while dispatching output to OutputService")
		except Exception:
			logger.exception("Error while running agent graph")


__all__ = ["LLMService"]

