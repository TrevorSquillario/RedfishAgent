from typing import Any, Dict, List
import os
import yaml
import logging
from utils.logging import setup_logger

from base.inventory_plugin_base import InventoryPluginInterface
from models.plugin import InventoryEntry

logger = setup_logger(__name__)

CONFIG_PATH = os.environ.get("INVENTORY_CONFIG_PATH", "/config/inventory.yaml")
REDFISH_EXPORTER_URL = os.environ.get("REDFISH_EXPORTER_URL", "http://idrac-exporter:9348")


class YamlInventoryPlugin(InventoryPluginInterface):
    plugin_type = "inventory"

    def __init__(self):
        self.config_path = CONFIG_PATH
        self.redfish_exporter_url = REDFISH_EXPORTER_URL

    def initialize(self):
        self.context = None

    def load(self) -> List[InventoryEntry]:
        """Load YAML inventory and convert to Prometheus HTTP SD format."""
        path = self.config_path
        logger.debug("Loading inventory from %s", path)
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning("Inventory file not found: %s", path)
            return []

        if data is None:
            logger.debug("Inventory file %s parsed to no data", path)
            return []

        out: List[InventoryEntry] = []

        if isinstance(data, dict):
            # try to handle a top-level dict with `targets` key
            if "targets" in data and isinstance(data["targets"], list):
                return self.normalize_entries(data["targets"])
            # otherwise wrap
            data = [data]

        if isinstance(data, list):
            out = self.normalize_entries(data)

        logger.info("Loaded %d inventory entries (normalized=%d)", len(data) if data is not None else 0, len(out))

        return out

    def normalize_entries(self, entries: List[Any]) -> List[InventoryEntry]:
        logger.debug("Normalizing %d entries", len(entries))
        results: List[InventoryEntry] = []
        for item in entries:
            if isinstance(item, str):
                logger.debug("Found string target entry: %s", item)
                results.append(InventoryEntry(targets=[self.format_target(item)]))
                continue

            if isinstance(item, dict):
                # If already in Prom HTTP SD form
                if "targets" in item:
                    logger.debug("Item already in HTTP SD form: keys=%s", list(item.keys()))
                    t = item.get("targets") or []
                    labels = item.get("labels") or {}
                    targets = [self.format_target(x) for x in self.ensure_list_of_str(t)]
                    results.append(InventoryEntry(targets=targets, labels=labels if labels else None))
                    continue

                # Try to build a target from host/ip/name + port
                host = item.get("host") or item.get("address") or item.get("ip") or item.get("name")
                port = item.get("port", 443) or item.get("metrics_port", 443)
                labels = item.get("labels") or {}
                if host and port:
                    target = f"{host}:{port}"
                    logger.debug("Built target from host/port: %s", target)
                    results.append(InventoryEntry(targets=[self.format_target(target)], labels=labels if labels else None))
                    continue

                # Fallback: any dict containing a `target` key
                if "target" in item:
                    logger.debug("Found fallback 'target' key in item")
                    results.append(InventoryEntry(targets=[self.format_target(x) for x in self.ensure_list_of_str(item.get("target"))], labels=labels if labels else None))
                    continue

            # ignore unknown types
        logger.debug("Normalization produced %d results", len(results))
        return results


    def format_target(self, target: str) -> str:
        """Prefix the REDFISH_EXPORTER_URL and format the target query param."""
        try:
            t = str(target)
        except Exception:
            t = ""

        # If it's already an absolute URL, return as-is
        if t.startswith("http://") or t.startswith("https://"):
            return t

        # strip optional port from host:port form
        host = t.split(":")[0]

        base = self.redfish_exporter_url.rstrip("/")
        formatted = f"{base}/metrics?target={host}"
        logger.debug("Formatted target '%s' -> '%s'", target, formatted)
        return formatted


    def ensure_list_of_str(self, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(x) for x in value]
        return [str(value)]
