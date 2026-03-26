import importlib
import pkgutil
import inspect
from typing import Dict, Type, Optional, Any, List
from utils.logging import setup_logger

# module logger
logger = setup_logger(__name__)

class PluginLoader:
    def __init__(self, plugin_package: str):
        self.plugin_package = plugin_package
        self.plugins: Dict[str, Any] = {}

    def load_plugins(self, context: Optional[Dict[str, Any]] = None):
        """
        Discovers and imports plugins from the specified package.
        Accepts an optional 'context' dictionary for dependency injection.
        """
        # Default to an empty dictionary if no context is provided
        if context is None:
            context = {}

        try:
            # Import the base package to get its path
            pkg = importlib.import_module(self.plugin_package)
        except ImportError as e:
            logger.error(f"Critical: Could not find plugin package '{self.plugin_package}'. {e}")
            return

        # Try to import the project's base interfaces (do inside method to
        # avoid potential circular imports). If unavailable, fall back to None.
        try:
            from redfishagent.base.inventory_plugin_base import InventoryPluginInterface
        except Exception:
            InventoryPluginInterface = None

        try:
            from redfishagent.base.output_plugin_base import OutputPluginInterface
        except Exception:
            OutputPluginInterface = None

        # Iterate through subdirectories (submodules)
        for loader, module_name, is_pkg in pkgutil.iter_modules(pkg.__path__):
            if is_pkg:
                full_module_name = f"{self.plugin_package}.{module_name}"
                
                # 1. ERROR HANDLING: Isolate module loading
                try:
                    module = importlib.import_module(full_module_name)
                except Exception as e:
                    logger.error(f"❌ Failed to load module '{full_module_name}': {e}")
                    continue  # Skip this broken module and move to the next one
                
                # Find classes that implement one of the project's plugin
                # interfaces, or which declare a `plugin_type` attribute.
                for name, obj in inspect.getmembers(module):
                    if not inspect.isclass(obj):
                        continue

                    if obj is None:
                        continue

                    is_candidate = False

                    # If class declares a plugin_type attribute, accept it
                    if getattr(obj, "plugin_type", None) in ("inventory", "output"):
                        is_candidate = True

                    # If it subclasses one of the known interfaces, accept it
                    if (InventoryPluginInterface is not None and issubclass(obj, InventoryPluginInterface) and obj is not InventoryPluginInterface):
                        is_candidate = True
                    if (OutputPluginInterface is not None and issubclass(obj, OutputPluginInterface) and obj is not OutputPluginInterface):
                        is_candidate = True

                    if not is_candidate:
                        continue

                    # 2. ERROR HANDLING: Isolate plugin initialization
                    try:
                        logger.info(f"✨ Loading plugin: {name} from {module_name}")
                        instance = obj()

                        # 3. DEPENDENCY INJECTION: Call initialize with context
                        # only if the method accepts parameters.
                        try:
                            sig_init = inspect.signature(instance.initialize)
                            if len(sig_init.parameters) == 0:
                                instance.initialize()
                            else:
                                instance.initialize(context)
                        except Exception:
                            # If initialize is not present or fails signature
                            # inspection, attempt best-effort call.
                            try:
                                instance.initialize(context)
                            except TypeError:
                                instance.initialize()

                        # Store by module name (package directory) so the key
                        # matches the convention used in config (e.g. "yaml")
                        self.plugins[module_name] = instance

                    except Exception as e:
                        logger.error(f"❌ Failed to initialize plugin '{name}': {e}")

    def get_plugin(self, name: str) -> Optional[Any]:
        return self.plugins.get(name)

    def run_all(self):
        """Executes the run method on all successfully loaded plugins."""
        for name, plugin in self.plugins.items():
            # 5. ERROR HANDLING: Don't let one bad run() crash the loop
            try:
                logger.info(f"▶️ Running {name}...")
                plugin.load()
            except Exception as e:
                logger.error(f"⚠️ Plugin '{name}' crashed during execution: {e}")

    def run_inventory(self) -> List[Dict[str, Any]]:
        """Run all inventory plugins (no input argument).

        Detection rules:
        - If a plugin has attribute `plugin_type == 'inventory'` it will be treated
          as an inventory plugin.
        - Otherwise, if the plugin is an instance of `InventoryPluginInterface`
          (from the project's base), it will be treated as inventory.

        Returns a mapping of plugin name -> result or exception info.
        """
        results: List[Dict[str, Any]] = []
        # import here to avoid circular imports when module is loaded
        try:
            from redfishagent.base.inventory_plugin_base import InventoryPluginInterface
        except Exception:
            InventoryPluginInterface = None

        for name, plugin in self.plugins.items():
            is_inventory = False
            if getattr(plugin, "plugin_type", None) == "inventory":
                is_inventory = True
            elif InventoryPluginInterface is not None and isinstance(plugin, InventoryPluginInterface):
                is_inventory = True

            if not is_inventory:
                continue

            try:
                logger.info(f"▶️ Running inventory plugin {name}...")
                res = plugin.load()
            except Exception as e:
                logger.error(f"⚠️ Inventory plugin '{name}' failed: {e}")
                continue

            if not res:
                continue

            # Accept lists or single entries
            entries = res if isinstance(res, list) else [res]

            for entry in entries:
                entry_obj = None
                try:
                    if hasattr(entry, "dict"):
                        entry_obj = entry.dict()
                    elif isinstance(entry, dict):
                        entry_obj = entry
                    else:
                        # Try to build from attributes
                        targets = getattr(entry, "targets", None)
                        labels = getattr(entry, "labels", None)
                        if targets is not None:
                            entry_obj = {"targets": list(targets) if not isinstance(targets, str) else [targets]}
                            if labels:
                                entry_obj["labels"] = labels
                except Exception:
                    entry_obj = None

                if entry_obj is None:
                    # Skip unknown/invalid entries
                    continue

                results.append(entry_obj)

        return results

    def run_output(self, input_data: Any = None) -> Dict[str, Any]:
        """Run all output plugins by calling their `send()` method.

        Detection rules:
        - If a plugin has attribute `plugin_type == 'output'` it will be treated
          as an output plugin.
        - Otherwise, if the plugin is an instance of `OutputPluginInterface`
          (from the project's base), it will be treated as output.

        If `send()` accepts parameters, `input_data` will be passed through.

        Returns a mapping of plugin name -> result or exception info.
        """
        results: Dict[str, Any] = {}
        try:
            from redfishagent.base.output_plugin_base import OutputPluginInterface
        except Exception:
            OutputPluginInterface = None

        for name, plugin in self.plugins.items():
            is_output = False
            if getattr(plugin, "plugin_type", None) == "output":
                is_output = True
            elif OutputPluginInterface is not None and isinstance(plugin, OutputPluginInterface):
                is_output = True

            if not is_output:
                continue

            try:
                logger.info(f"▶️ Running output plugin {name}...")
                sig = inspect.signature(plugin.send)
                if len(sig.parameters) == 0:
                    res = plugin.send()
                else:
                    res = plugin.send(input_data)
                results[name] = {"result": res}
            except Exception as e:
                results[name] = {"error": str(e)}

        return results