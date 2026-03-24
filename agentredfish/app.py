from typing import Optional, List, Any
from utils.logging import setup_logger

import os
from pathlib import Path

# local services
from services.config import ConfigService
from services.plugin_loader import PluginLoader

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

        self._initialized = True



