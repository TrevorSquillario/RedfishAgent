"""LLM-related services."""
from typing import Any, Dict, Optional, List
import logging
import os
import json

import aiohttp

from models.app import LogEntry, ConfigModel, PluginsConfig
from .inventory import InventoryService

logger = logging.getLogger(__name__)


class LLMService:
	def __init__(self, config: ConfigModel, inventory_service: Optional[InventoryService] = None, output_service: Optional[Any] = None):
		self.config = config
		self.inventory = inventory_service
		self.output = output_service
		# Read OpenAI-related env vars
		self.openai_api_key = os.getenv("OPENAI_API_KEY")
		self.openai_api_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1/chat/completions")
		self.openai_model = os.getenv("OPENAI_MODEL", "hermes-agent")

	async def run_agent(self, log_entry: Optional[LogEntry] = None) -> None:
		"""Run a single LLM request using prompts from the config.

		This sends a single POST to the configured OpenAI-compatible URL
		using `aiohttp`. It builds the `system` and `user` messages from
		`self.config.prompts.system` and
		`self.config.prompts.redfish_agent.main`, and includes the
		`log_entry` in the user content.
		"""
		# Extract configured prompts
		system_prompt = ""
		redfish_main_prompt = ""
		try:
			system_prompt = self.config.prompts.get("system", "")
		except Exception:
			logger.debug("No system prompt found in config.prompts")
		try:
			redfish_main_prompt = self.config.prompts.get("redfish_agent", {}).get("main", "")
		except Exception:
			logger.debug("No redfish_agent.main prompt found in config.prompts")

		# Prepare user content including the log entry
		user_prompt = redfish_main_prompt or ""
		if log_entry is not None:
			log_entry_prompt = (
				f"Alert triggered host {getattr(log_entry, 'source', '')}.\n"
				f"Event Timestamp: {getattr(log_entry, 'event_timestamp', '')}\n"
				f"Event ID: {getattr(log_entry, 'event_id', '')}\n"
				f"Message ID: {getattr(log_entry, 'message_id', '')}\n"
				f"Message: {getattr(log_entry, 'message', '')}\n"
				f"Labels: {getattr(log_entry, 'labels', {})}\n"
				f"{user_prompt}"
			)

		payload = {
			"model": self.openai_model,
			"messages": [
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": log_entry_prompt},
			],
			"stream": False,
		}

		headers = {
			"Content-Type": "application/json",
		}
		if self.openai_api_key:
			headers["Authorization"] = f"Bearer {self.openai_api_key}"

		async with aiohttp.ClientSession() as session:
			try:
				async with session.post(self.openai_api_base, json=payload, headers=headers) as resp:
					text = await resp.text()
					logger.debug(f"LLM request status: {resp.status}")
					try:
						resp_json = json.loads(text)
					except Exception:
						resp_json = {"raw": text}
					logger.debug(f"LLM response: {resp_json}")

					# If an OutputService was injected, send the assistant text
					try:
						assistant_text = ""
						if isinstance(resp_json, dict):
							choices = resp_json.get("choices") or []
							if choices and isinstance(choices, list):
								first = choices[0]
								msg = first.get("message") if isinstance(first, dict) else None
								if isinstance(msg, dict):
									assistant_text = msg.get("content", "")
								else:
									assistant_text = first.get("text", "") if isinstance(first, dict) else ""
						if getattr(self, "output", None) is not None:
							try:
								self.output.run_output(assistant_text)
								logger.info("Sent LLM assistant text to OutputService")
							except Exception as e:
								logger.exception(f"Failed sending LLM assistant text to OutputService: {e}")
					except Exception as e:
						logger.exception(f"Error while calling LLM endpoint: {e}")
			except Exception as e:
				logger.exception(f"Unexpected error while preparing or sending LLM request: {e}")

__all__ = ["LLMService"]
