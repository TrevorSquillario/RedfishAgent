import importlib
import importlib.util
import json
import pkgutil
import inspect
import os
import sys
import subprocess
from typing import Dict, Type, Optional, Any, List
from utils.logging import setup_logger

# module logger
logger = setup_logger(__name__)


class PluginLoader:
    def __init__(self, plugin_package: str):
        self.plugin_package = plugin_package
        self.plugins: Dict[str, Any] = {}

    def load_plugins(self, context: Optional[Dict[str, Any]] = None, allowed_plugins: Optional[List[str]] = None):
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
            from base.inventory_plugin_base import InventoryPluginInterface
        except Exception:
            InventoryPluginInterface = None

        try:
            from base.output_plugin_base import OutputPluginInterface
        except Exception:
            OutputPluginInterface = None

        try:
            from base.trigger_plugin_base import TriggerPluginInterface
        except Exception:
            TriggerPluginInterface = None

        # Iterate through subdirectories (submodules)
        for loader, module_name, is_pkg in pkgutil.iter_modules(pkg.__path__):
            # If caller provided an allow-list, skip modules not in it
            if allowed_plugins is not None and module_name not in allowed_plugins:
                logger.debug("Skipping plugin '%s' because it's not in allowed list", module_name)
                continue
            if is_pkg:
                full_module_name = f"{self.plugin_package}.{module_name}"
                
                # Ensure plugin dependencies are installed into an isolated
                # target directory before importing the module. Use the
                # package path as a cache location for per-plugin targets.
                try:
                    try:
                        spec = importlib.util.find_spec(full_module_name)
                        if spec and spec.submodule_search_locations:
                            plugin_dir = spec.submodule_search_locations[0]
                        else:
                            plugin_dir = os.path.join(pkg.__path__[0], module_name)
                    except Exception:
                        plugin_dir = os.path.join(pkg.__path__[0], module_name)

                    self.ensure_plugin_deps(plugin_dir)
                except Exception:
                    # Non-fatal: proceed to attempt importing the module
                    pass

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
                    if getattr(obj, "plugin_type", None) in ("inventory", "output", "trigger"):
                        is_candidate = True

                    # If it subclasses one of the known interfaces, accept it
                    if (InventoryPluginInterface is not None and issubclass(obj, InventoryPluginInterface) and obj is not InventoryPluginInterface):
                        is_candidate = True
                    if (OutputPluginInterface is not None and issubclass(obj, OutputPluginInterface) and obj is not OutputPluginInterface):
                        is_candidate = True
                    if (TriggerPluginInterface is not None and issubclass(obj, TriggerPluginInterface) and obj is not TriggerPluginInterface):
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

    def ensure_plugin_deps(self, plugin_dir: str):
        """Install plugin requirements into the current Python environment.

        Reads `requirements.txt` from `plugin_dir` and runs
        `python -m pip install -r requirements.txt`. This does not create any
        extra directories or modify `sys.path`.
        """
        req = os.path.join(plugin_dir, "requirements.txt")
        if not os.path.isfile(req):
            return

        try:
            with open(req, "rb") as f:
                _ = f.read()
        except Exception as e:
            logger.error(f"Could not read requirements for plugin at {plugin_dir}: {e}")
            return

        logger.info(f"Installing plugin deps from {req} into current environment")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req])
        except Exception as e:
            logger.error(f"Failed to install plugin deps for {plugin_dir}: {e}")

    def get_plugin(self, name: str) -> Optional[Any]:
        return self.plugins.get(name)

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
            from base.inventory_plugin_base import InventoryPluginInterface
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
            from base.output_plugin_base import OutputPluginInterface
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
                res = plugin.send(input_data)
                results[name] = {"result": res}
            except Exception as e:
                results[name] = {"error": str(e)}

        logger.debug(f"Output plugin results: {results}")
        return results

    def run_trigger(self) -> List["threading.Thread"]:
        """Start all trigger plugins in background threads.

        Detection rules:
        - If a plugin has attribute `plugin_type == 'trigger'` it will be treated
          as a trigger plugin.
        - Otherwise, if the plugin is an instance of `TriggerPluginInterface`
          (from the project's base), it will be treated as a trigger.

        Returns a list of `threading.Thread` objects for the started triggers.
        """
        try:
            from base.trigger_plugin_base import TriggerPluginInterface
        except Exception:
            TriggerPluginInterface = None

        import threading
        import asyncio

        self._trigger_threads: List[threading.Thread] = []

        for name, plugin in self.plugins.items():
            is_trigger = False
            if getattr(plugin, "plugin_type", None) == "trigger":
                is_trigger = True
            elif TriggerPluginInterface is not None and isinstance(plugin, TriggerPluginInterface):
                is_trigger = True

            if not is_trigger:
                continue

            def _start(p=plugin, n=name):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(p.run())
                except Exception:
                    logger.exception("Trigger plugin '%s' exited with error", n)
                finally:
                    try:
                        loop.run_until_complete(loop.shutdown_asyncgens())
                    except Exception:
                        pass
                    loop.close()

            t = threading.Thread(target=_start, daemon=True, name=f"trigger-{name}")
            self._trigger_threads.append(t)
            t.start()
            logger.info("Trigger plugin '%s' started in background", name)

        return self._trigger_threads