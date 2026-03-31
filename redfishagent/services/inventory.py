("""Inventory service wrapper around the project's plugin loader.

Provides a thin `InventoryService` class that loads plugins from a
package and invokes the `run_inventory` method on the project's
`PluginLoader`.
""")

from typing import Any, Dict, Optional, List
import os
from utils.logging import setup_logger

from .plugin_loader import PluginLoader

logger = setup_logger(__name__)


class InventoryService:
	"""Service for running inventory plugins.

	Args:
		plugin_package: Python package path where inventory plugins live.
		context: Optional context dict to pass to plugins during initialization.
	"""

	def __init__(self, plugin_package: str = "redfishagent.plugins.inventory", plugin_loader: Optional[PluginLoader] = None, context: Optional[Dict[str, Any]] = None):
		self.plugin_package = plugin_package
		self.context = context or {}
		# Accept an existing PluginLoader instance (preferred), otherwise create one
		if plugin_loader is not None:
			self.loader = plugin_loader
		else:
			self.loader = PluginLoader(self.plugin_package)

	def run_inventory(self, add_exporter_url: bool = True) -> List[Dict[str, Any]]:
		"""Discover, load and run all inventory plugins.

		Args:
			add_exporter_url: If True, prefix non-HTTP targets with the
				REDFISH_EXPORTER_URL and format as the exporter metrics URL.

		Returns:
			List of inventory entries as dicts produced by `PluginLoader.run_inventory()`.
		"""
		try:
			logger.info("InventoryService: running inventory plugins from %s", self.plugin_package)
			# Only load plugins if none are present on the loader (avoid double-loading)
			if not getattr(self.loader, "plugins", None):
				logger.debug("No plugins loaded on loader, loading with context")
				self.loader.load_plugins(self.context)
			results = self.loader.run_inventory()

			if not add_exporter_url:
				return results

			# Prefix non-HTTP targets with the REDFISH_EXPORTER_URL
			base = os.environ.get("REDFISH_EXPORTER_URL", "http://idrac-exporter:9348").rstrip("/")
			out: List[Dict[str, Any]] = []
			for entry in results:
				try:
					targets = entry.get("targets", [])
				except Exception:
					out.append(entry)
					continue

				new_targets = []
				for t in targets:
					try:
						s = str(t)
					except Exception:
						s = ""

					if s.startswith("http://") or s.startswith("https://"):
						new_targets.append(s)
						continue

					host = s.split(":")[0]
					formatted = f"{base}/metrics?target={host}"
					new_targets.append(formatted)

				new_entry = dict(entry)
				new_entry["targets"] = new_targets
				out.append(new_entry)

			return out
		except Exception as exc:  # pragma: no cover - defensive fallback
			logger.exception("InventoryService failed: %s", exc)
			return [{"error": str(exc)}]

