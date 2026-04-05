"""LLM-related services."""
from typing import Any, Dict, Optional, List
import logging
import os
import json

import openai

from models.app import LogEntry, ConfigModel, PluginsConfig
from .inventory import InventoryService

logger = logging.getLogger(__name__)

class LLMService:
	def __init__(self, config: ConfigModel, db_service: Optional[Any] = None, inventory_service: Optional[InventoryService] = None, output_service: Optional[Any] = None):
		self.db = db_service
		self.config = config
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

	async def run_agent(self, log_entry: Optional[LogEntry] = None) -> None:
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

