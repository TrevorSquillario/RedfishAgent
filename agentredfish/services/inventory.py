("""Inventory service wrapper around the project's plugin loader.

Provides a thin `InventoryService` class that loads plugins from a
package and invokes the `run_inventory` method on the project's
`PluginLoader`.
""")

from typing import Any, Dict, Optional, List
from utils.logging import setup_logger

from .plugin_loader import PluginLoader

logger = setup_logger(__name__)


class InventoryService:
	"""Service for running inventory plugins.

	Args:
		plugin_package: Python package path where inventory plugins live.
		context: Optional context dict to pass to plugins during initialization.
	"""

	def __init__(self, plugin_package: str = "agentredfish.plugins.inventory", context: Optional[Dict[str, Any]] = None):
		self.plugin_package = plugin_package
		self.context = context or {}
		self.loader = PluginLoader(self.plugin_package)

	def run_inventory(self) -> List[Dict[str, Any]]:
		"""Discover, load and run all inventory plugins.

		Returns:
			Mapping of plugin name -> result or error information as produced
			by `PluginLoader.run_inventory()`.
		"""
		try:
			logger.info("InventoryService: loading plugins from %s", self.plugin_package)
			self.loader.load_plugins(self.context)
			logger.info("InventoryService: running inventory plugins")
			return self.run_inventory()
		except Exception as exc:  # pragma: no cover - defensive fallback
			logger.exception("InventoryService failed: %s", exc)
			return {"error": str(exc)}

