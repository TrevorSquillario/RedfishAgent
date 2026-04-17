from typing import Any, Dict, Optional
import asyncio
import os

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.socket_mode.request import SocketModeRequest

from base.trigger_plugin_base import TriggerPluginInterface
from utils.logging import setup_logger

logger = setup_logger(__name__)

#
# Slack Socket Mode Setup https://docs.slack.dev/tools/python-slack-sdk/socket-mode/
#
class SlackReplyTriggerPlugin(TriggerPluginInterface):
    plugin_type = "trigger"

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._app_token: Optional[str] = None
        self._socket_client: Optional[SocketModeClient] = None
        self._running: bool = False

    def initialize(self, context: Dict[str, Any]) -> None:
        """Read Slack credentials from the environment.

        Required environment variables:
        - ``SLACK_BOT_TOKEN``: bot token (xoxb-)
        - ``SLACK_APP_TOKEN``: app-level token (xapp-) with connections:write scope
        """
        self._token = os.getenv("SLACK_BOT_TOKEN")
        if not self._token:
            raise RuntimeError("SLACK_BOT_TOKEN environment variable not set")

        self._app_token = os.getenv("SLACK_APP_TOKEN")
        if not self._app_token:
            raise RuntimeError("SLACK_APP_TOKEN environment variable not set")

        logger.info("SlackReplyTriggerPlugin initialized")

    async def run(self) -> None:
        """Connect to Slack via Socket Mode and listen for events_api payloads."""
        self._running = True

        self._socket_client = SocketModeClient(
            app_token=self._app_token,
            web_client=AsyncWebClient(token=self._token),
        )

        async def process(client: SocketModeClient, req: SocketModeRequest) -> None:
            if req.type == "events_api":
                response = SocketModeResponse(envelope_id=req.envelope_id)
                await client.send_socket_mode_response(response)
                logger.info("Received Slack events_api payload: %s", req.payload)

        self._socket_client.socket_mode_request_listeners.append(process)
        await self._socket_client.connect()
        logger.info("Slack Socket Mode connected")

        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            await self._socket_client.close()
            logger.info("Slack Socket Mode disconnected")

    def stop(self) -> None:
        """Signal the run loop to exit."""
        self._running = False
