"""Redis stream trigger plugin.

Recreates the ``subscribe_to_alerts`` behaviour from ``LLMService`` as a
standalone trigger plugin so it can be loaded, configured and replaced via
the plugin system without touching core services.
"""
from typing import Any, Dict, Optional
import json
import logging
import os
import time

import redis as redis_lib

from base.trigger_plugin_base import TriggerPluginInterface
from models.app import LogEntry
from utils.logging import setup_logger

logger = setup_logger(__name__)


class RedisRedfishListenerGoTriggerPlugin(TriggerPluginInterface):
    plugin_type = "trigger"

    def __init__(self) -> None:
        self._llm_service: Optional[Any] = None
        self._redis_client: Optional[Any] = None
        self._stream_name: str = "redfish_events"
        self._running: bool = False

    # ------------------------------------------------------------------
    # TriggerPluginInterface
    # ------------------------------------------------------------------

    def initialize(self, context: dict) -> None:
        """Pull dependencies from *context*.

        Required keys:
        - ``llm_service``: a :class:`services.llm.LLMService` instance.

        Optional keys (fall back to environment variables / defaults):
        - ``redis_client``: an already-connected redis client to use instead of
          creating one from environment variables.
        - ``stream_name``: Redis stream key (default: ``REDIS_STREAM`` env var
          or ``redfish_events``).

        Redis connection is configured from environment variables:
        - ``REDIS_HOST``     (default: ``redis``)
        - ``REDIS_PORT``     (default: ``6379``)
        - ``REDIS_DB``       (default: ``0``)
        - ``REDIS_PASSWORD`` (default: none)
        """
        self._llm_service = context.get("llm_service")
        if self._llm_service is None:
            raise RuntimeError("RedisCustomTriggerPlugin requires 'llm_service' in context")

        # Allow an explicit client to be injected; otherwise build one from env.
        self._redis_client = context.get("redis_client")
        if self._redis_client is None:
            host = os.getenv("REDIS_HOST", "redis")
            port = int(os.getenv("REDIS_PORT", "6379"))
            db = int(os.getenv("REDIS_DB", "0"))
            password = os.getenv("REDIS_PASSWORD") or None
            self._redis_client = redis_lib.Redis(
                host=host, port=port, db=db, password=password, decode_responses=True
            )

        self._stream_name = context.get("stream_name") or os.getenv("REDIS_STREAM", "redfish_events")
        logger.info("RedisRedfishListenerGoTriggerPlugin initialized (stream=%s)", self._stream_name)

    async def run(self) -> None:
        """Read from the Redis stream and dispatch each entry to the LLM agent."""
        if self._redis_client is None:
            logger.error("RedisRedfishListenerGoTriggerPlugin: Redis client not available, cannot run")
            return

        llm_service = self._llm_service
        inventory_service = getattr(llm_service, "inventory", None)
        stream_name = self._stream_name
        last_id = "$"
        self._running = True
        block_ms = 1000

        logger.info("RedisRedfishListenerGoTriggerPlugin: starting stream listener on '%s'", stream_name)
        try:
            while self._running:
                try:
                    results = self._redis_client.xread({stream_name: last_id}, block=block_ms, count=10)
                    if not results:
                        continue

                    for _stream, entries in results:
                        for entry_id, fields in entries:
                            def _get_field(key: str):
                                if key in fields:
                                    return fields[key]
                                bkey = key.encode()
                                if bkey in fields:
                                    return fields[bkey]
                                return None

                            raw_payload = _get_field("payload")
                            raw_source = _get_field("source")

                            try:
                                payload_str = raw_payload.decode("utf-8") if isinstance(raw_payload, (bytes, bytearray)) else str(raw_payload)
                            except Exception:
                                payload_str = ""
                            try:
                                source = raw_source.decode("utf-8") if isinstance(raw_source, (bytes, bytearray)) else str(raw_source)
                            except Exception:
                                source = ""

                            try:
                                payload_obj: Dict[str, Any] = json.loads(payload_str) if payload_str else {}
                            except Exception:
                                logger.exception("Failed to parse payload JSON for entry %s", entry_id)
                                payload_obj = {"raw": payload_str}

                            labels: Dict[str, Any] = {}
                            try:
                                if inventory_service is not None:
                                    inv = inventory_service.get_inventory()
                                    for inv_entry in inv:
                                        targets = inv_entry.get("targets", []) or []
                                        for t in targets:
                                            match_name = str(t).split(":")[0]
                                            if match_name and match_name == source:
                                                labels = inv_entry.get("labels", {}) or {}
                                                break
                                        if labels:
                                            break
                            except Exception:
                                logger.exception("Failed looking up inventory labels for source %s", source)

                            entry_obj = LogEntry(source=source, payload=payload_obj, labels=labels)
                            logger.info("LogEntry %s -> %s", entry_id, entry_obj)
                            last_id = entry_id

                            try:
                                await llm_service.run_agent(log_entry=entry_obj)
                                try:
                                    self._redis_client.xdel(stream_name, entry_id)
                                    logger.debug("Deleted stream entry %s from %s", entry_id, stream_name)
                                except Exception:
                                    logger.exception("Failed to delete stream entry %s from %s", entry_id, stream_name)
                            except Exception:
                                logger.exception("Error running agent for entry %s", entry_id)

                except Exception:
                    logger.exception("Error reading from Redis stream %s", stream_name)
                    time.sleep(1)
        finally:
            self._running = False
            logger.info("RedisRedfishListenerGoTriggerPlugin: stream listener stopped")

    def stop(self) -> None:
        """Signal the run loop to exit."""
        self._running = False
