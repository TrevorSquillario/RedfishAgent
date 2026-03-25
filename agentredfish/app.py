from typing import Optional, List, Any
from utils.logging import setup_logger

import os
from pathlib import Path

# local services
from services.config import ConfigService
from services.plugin_loader import PluginLoader
from services.redis import RedisService
from services.webhook import WebhookService
from services.llm import LLMService
from services.database import init_database
import os

class AgentFishApp:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AgentFishApp, cls).__new__(cls)
            # Initialize the singleton instance
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the AgentFishApp with required services (only once)"""
        if self._initialized:
            return
        # initialize logger
        self.logger = setup_logger("agentredfish.app")

        # Load configuration (may fail if config file not present)
        try:
            self.config_service = ConfigService()
            self.logger.info("ConfigService loaded")
            config_model = getattr(self.config_service, "config", None)
        except Exception as e:
            self.logger.warning(f"Could not load ConfigService: {e}")
            self.config_service = None
            config_model = None

        # Determine plugin names requested in config (if available)
        inventory_spec = []
        output_spec = []
        if config_model and getattr(config_model, "plugins", None):
            inventory_spec = list(getattr(config_model.plugins, "inventory", []) or [])
            output_spec = list(getattr(config_model.plugins, "output", []) or [])

        # Initialize plugin loaders for inventory and output packages
        self.inventory_loader = PluginLoader("plugins.inventory")
        self.output_loader = PluginLoader("plugins.output")

        # Ensure plugin loader modules use our logger (they expect a `logger` symbol)
        try:
            import services.plugin_loader as _pl_mod
            _pl_mod.logger = self.logger
        except Exception:
            # best-effort: continue even if we can't inject logger
            pass

        # Context passed to plugins during initialization
        context = {"config": config_model, "logger": self.logger, "app": self}

        # Discover and initialize plugins
        try:
            self.inventory_loader.load_plugins(context=context)
        except Exception as e:
            self.logger.error(f"Failed loading inventory plugins: {e}")

        try:
            self.output_loader.load_plugins(context=context)
        except Exception as e:
            self.logger.error(f"Failed loading output plugins: {e}")

        # If config specified explicit plugin lists, filter loaded plugins
        if inventory_spec:
            self.inventory_loader.plugins = {k: v for k, v in self.inventory_loader.plugins.items() if k in inventory_spec}
        if output_spec:
            self.output_loader.plugins = {k: v for k, v in self.output_loader.plugins.items() if k in output_spec}

        # Initialize RedisService and WebhookService
        try:
            redis_host = os.getenv("REDIS_HOST", "redis")
            redis_port = int(os.getenv("REDIS_PORT", "6379"))
            redis_db = int(os.getenv("REDIS_DB", "0"))
            redis_password = os.getenv("REDIS_PASSWORD", None)

            self.redis_service = RedisService(host=redis_host, port=redis_port, db=redis_db, password=redis_password)
            self.logger.info("RedisService initialized (%s:%s db=%s)", redis_host, redis_port, redis_db)
        except Exception as e:
            self.logger.warning(f"Could not initialize RedisService: {e}")
            self.redis_service = None

        try:
            self.webhook_service = WebhookService(self.redis_service)
            self.logger.info("WebhookService initialized")
        except Exception as e:
            self.logger.warning(f"Could not initialize WebhookService: {e}")
            self.webhook_service = None

        # Initialize LLMService and start background listener for alerts
        try:
            self.llm_service = LLMService(self.redis_service)
            self.logger.info("LLMService initialized")
            try:
                import threading

                listener = threading.Thread(
                    target=self.llm_service.subscribe_to_alerts,
                    kwargs={"start_id": "$"},
                    daemon=True,
                )
                listener.start()
                self.logger.info("LLMService subscribe_to_alerts started in background")
            except Exception:
                self.logger.exception("Failed to start LLMService listener thread")
        except Exception as e:
            self.logger.warning(f"Could not initialize LLMService: {e}")
            self.llm_service = None

        # Initialize DatabaseManager (services/database.py)
        try:
            self.db_manager = init_database()
            self.logger.info("DatabaseManager initialized")
        except Exception as e:
            self.logger.warning(f"Could not initialize DatabaseManager: {e}")
            self.db_manager = None

        self._initialized = True



