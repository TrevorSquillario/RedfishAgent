from typing import Optional
import os
import logging

from utils.logging import setup_logger
from base.output_plugin_base import OutputPluginInterface

from slack_sdk import WebClient
from slack_blocks_markdown import markdown_to_blocks

logger = setup_logger(__name__)

class SlackOutputPlugin(OutputPluginInterface):
	"""Output plugin that sends messages to a Slack

	Expects the `SLACK_BOT_TOKEN` and `SLACK_CHANNEL_ID` environment variables to be set.
	"""

	def __init__(self) -> None:
		self.token: Optional[str] = None
		self.channel_id: Optional[str] = None

	def initialize(self) -> None:
		"""Read configuration from the environment and validate it."""
		self.token =  os.getenv("SLACK_BOT_TOKEN")
		if not self.token:
			raise RuntimeError("SLACK_BOT_TOKEN environment variable not set")

		self.channel_id =  os.getenv("SLACK_CHANNEL_ID")
		if not self.channel_id:
			raise RuntimeError("SLACK_CHANNEL_ID environment variable not set")

		logger.info("Slack Output Plugin Initialized")

	def send(self, input_data: str):
		"""Send `input_data` as a Slack message using the Slack Python SDK

		The simplest payload uses the `text` field. If you need richer
		formatting, pass Slack-compatible JSON as the message text or update
		this method to construct `blocks` / attachments.
		"""
		payload = markdown_to_blocks(input_data)
		headers = {"Content-Type": "application/json"}
		metadata = {
			"event_type": "redfishagent_message",
			"event_payload": {
				"thread_id": 1 
			}
		}

		try:
			logger.debug(f"Slack message payload: {payload}")
			logger.debug(f"Slack message metadata: {metadata}")

			# Use blocks directly with Slack SDK

			client = WebClient(token=self.token)
			result = client.chat_postMessage(
				channel=self.channel_id,
				blocks=payload,
				metadata=metadata
			)
			logger.debug(f"{result}")
			# Extract timestamps from Slack response
			if isinstance(result, dict):
				ts = result.get("ts")
				message_ts = result.get("message", {}).get("ts")
			else:
				ts = None
				message_ts = None
			logger.info("Slack message sent: ts=%s, message.ts=%s", ts, message_ts)
		except Exception as e:  # covers connection and HTTP errors
			logger.exception("Failed to send Slack message: %s", e)
			raise

		return result
