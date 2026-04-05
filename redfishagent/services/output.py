"""Output service wrapper around the project's plugin loader.

Provides a thin `OutputService` class that loads plugins from a
package and invokes the `run_output` method on the project's
`PluginLoader`.
"""

from typing import Any, Dict, Optional
from utils.logging import setup_logger

from .plugin_loader import PluginLoader

logger = setup_logger(__name__)


class OutputService:
	"""Service for running output plugins.

	Args:
		plugin_package: Python package path where output plugins live.
		context: Optional context dict to pass to plugins during initialization.
	"""

	def __init__(self, plugin_package: str = "plugins.output", plugin_loader: Optional[PluginLoader] = None, context: Optional[Dict[str, Any]] = None):
		self.plugin_package = plugin_package
		self.context = context or {}
		# Accept an existing PluginLoader instance (preferred), otherwise create one
		if plugin_loader is not None:
			self.loader = plugin_loader
		else:
			self.loader = PluginLoader(self.plugin_package)

	def run_output(self, input_data: Any = None) -> Dict[str, Any]:
		"""Discover, load and run all output plugins.

		If the loader has no plugins, `load_plugins` will be invoked with the
		stored context. Returns the dictionary returned by `PluginLoader.run_output()`
		and handles exceptions by returning an error mapping.
		"""
		try:
			logger.info("OutputService: running output plugins from %s", self.plugin_package)
			if not getattr(self.loader, "plugins", None):
				logger.debug("No plugins loaded on loader, loading with context")
				self.loader.load_plugins(self.context)
			results = self.loader.run_output(input_data)
			return results
		except Exception as exc:  # pragma: no cover - defensive fallback
			logger.exception("OutputService failed: %s", exc)
			return {"error": str(exc)}
