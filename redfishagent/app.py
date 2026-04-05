from typing import Optional, List, Any, TYPE_CHECKING
from utils.logging import setup_logger

import os
from pathlib import Path

if TYPE_CHECKING:
    from fastapi import FastAPI

# local services
from services.config import ConfigService
from services.plugin_loader import PluginLoader
from services.inventory import InventoryService
from services.llm import LLMService
from services.output import OutputService
from services.database import init_database
import os

class RedfishAgentApp:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(RedfishAgentApp, cls).__new__(cls)
            # Initialize the singleton instance
            cls._instance._initialized = False
        return cls._instance
    
    
    def __init__(self, fastapi_app: Optional["FastAPI"] = None):
        """Initialize the RedfishAgentApp with required services (only once)"""
        if self._initialized:
            return
        self.fastapi_app = fastapi_app
        # initialize logger
        self.logger = setup_logger("app")

        # Load configuration (may fail if config file not present)
        try:
            self.config_service = ConfigService()
            self.logger.info("ConfigService loaded")
            config_model = getattr(self.config_service, "config", None)
            # Log the full parsed config for debugging/visibility
            try:
                import json

                try:
                    cfg_text = json.dumps(
                        config_model,
                        default=lambda o: getattr(o, "__dict__", str(o)),
                        indent=2,
                    )
                except TypeError:
                    cfg_text = str(config_model)
                self.logger.info("Full config:\n%s", cfg_text)
            except Exception:
                self.logger.exception("Failed to serialize/log config_model")
        except Exception as e:
            self.logger.warning(f"Could not load ConfigService: {e}")
            self.config_service = None
            config_model = None

        # Determine plugin names requested in config (if available)
        inventory_spec = []
        output_spec = []
        trigger_spec = []
        if config_model and getattr(config_model, "plugins", None):
            inventory_spec = list(getattr(config_model.plugins, "inventory", []) or [])
            output_spec = list(getattr(config_model.plugins, "output", []) or [])
            trigger_spec = list(getattr(config_model.plugins, "trigger", []) or [])

        # Initialize plugin loaders for inventory, output and trigger packages
        self.inventory_loader = PluginLoader("plugins.inventory")
        self.output_loader = PluginLoader("plugins.output")
        self.trigger_loader = PluginLoader("plugins.trigger")

        # Ensure plugin loader modules use our logger (they expect a `logger` symbol)
        try:
            import services.plugin_loader as _pl_mod
            _pl_mod.logger = self.logger
        except Exception:
            # best-effort: continue even if we can't inject logger
            pass

        # Initialize DatabaseManager (services/database.py)
        try:
            self.db_manager = init_database()
            self.logger.info("DatabaseManager initialized")
        except Exception as e:
            self.logger.warning(f"Could not initialize DatabaseManager: {e}")
            self.db_manager = None

        # Context passed to plugins during initialization
        context = {"config": config_model, "logger": self.logger, "app": self, "db": self.db_manager, "fastapi_app": self.fastapi_app}

        # Discover and initialize plugins
        try:
            self.inventory_loader.load_plugins(context=context, allowed_plugins=inventory_spec if inventory_spec else None)
        except Exception as e:
            self.logger.error(f"Failed loading inventory plugins: {e}")

        try:
            self.output_loader.load_plugins(context=context, allowed_plugins=output_spec if output_spec else None)
        except Exception as e:
            self.logger.error(f"Failed loading output plugins: {e}")

        # Trigger plugins are loaded after LLMService is ready (see below)

        # If config specified explicit plugin lists, filter loaded plugins
        if inventory_spec:
            self.inventory_loader.plugins = {k: v for k, v in self.inventory_loader.plugins.items() if k in inventory_spec}
        if output_spec:
            self.output_loader.plugins = {k: v for k, v in self.output_loader.plugins.items() if k in output_spec}

        # Create a shared InventoryService that uses the existing PluginLoader
        try:
            self.inventory_service = InventoryService(plugin_loader=self.inventory_loader, context=context)
            self.logger.info("InventoryService initialized and attached to app")
        except Exception as e:
            self.logger.warning(f"Could not initialize InventoryService: {e}")
            self.inventory_service = None

        # Create a shared OutputService that uses the existing PluginLoader
        try:
            self.output_service = OutputService(plugin_loader=self.output_loader, context=context)
            self.logger.info("OutputService initialized and attached to app")
        except Exception as e:
            self.logger.warning(f"Could not initialize OutputService: {e}")
            self.output_service = None

        # Initialize LLMService
        try:
            # pass the parsed config model (not the service instance)
            self.llm_service = LLMService(db_service=self.db_manager, config=config_model, inventory_service=self.inventory_service, output_service=self.output_service)
            self.logger.info("LLMService initialized")
        except Exception as e:
            self.logger.warning(f"Could not initialize LLMService: {e}")
            self.llm_service = None

        # Load and start trigger plugins (requires llm_service to be ready)
        try:
            trigger_context = {**context, "llm_service": self.llm_service, "fastapi_app": self.fastapi_app}
            self.trigger_loader.load_plugins(context=trigger_context, allowed_plugins=trigger_spec if trigger_spec else None)
            # Log loaded trigger plugins for visibility (like inventory/output)
            try:
                self.logger.info("Trigger plugins loaded: %s", list(self.trigger_loader.plugins.keys()))
            except Exception:
                self.logger.exception("Failed to log trigger plugins list")
            if trigger_spec:
                self.trigger_loader.plugins = {k: v for k, v in self.trigger_loader.plugins.items() if k in trigger_spec}
                try:
                    self.logger.info("Trigger plugins after filtering: %s", list(self.trigger_loader.plugins.keys()))
                except Exception:
                    self.logger.exception("Failed to log filtered trigger plugins list")

            # Delegate starting triggers to the PluginLoader helper which
            # creates background threads and returns them.
            try:
                self._trigger_threads = self.trigger_loader.run_trigger()
                try:
                    self.logger.info("Started %d trigger threads", len(self._trigger_threads) if getattr(self, "_trigger_threads", None) else 0)
                except Exception:
                    self.logger.exception("Failed to log trigger threads start")
            except Exception:
                self.logger.exception("Failed to start trigger plugins via PluginLoader.run_trigger")
        except Exception:
            self.logger.exception("Failed to load/start trigger plugins")

        self._initialized = True



