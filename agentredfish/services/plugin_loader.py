import importlib
import pkgutil
import inspect
from typing import Dict, Type, Optional, Any
from plugin_interface import PluginInterface

class PluginLoader:
    def __init__(self, plugin_package: str):
        self.plugin_package = plugin_package
        self.plugins: Dict[str, PluginInterface] = {}

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
                
                # Find classes that implement PluginInterface
                for name, obj in inspect.getmembers(module):
                    if (inspect.isclass(obj) and 
                        issubclass(obj, PluginInterface) and 
                        obj is not PluginInterface):
                        
                        # 2. ERROR HANDLING: Isolate plugin initialization
                        try:
                            logger.info(f"✨ Loading plugin: {name} from {module_name}")
                            instance = obj()
                            
                            # 3. DEPENDENCY INJECTION: Pass the context in
                            instance.initialize(context)
                            
                            # 4. BUG FIX: Key by class `name`, not `module_name`
                            self.plugins[name] = instance
                            
                        except Exception as e:
                            logger.error(f"❌ Failed to initialize plugin '{name}': {e}")

    def get_plugin(self, name: str) -> Optional[PluginInterface]:
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

    def run_inventory(self) -> Dict[str, Any]:
        """Run all inventory plugins (no input argument).

        Detection rules:
        - If a plugin has attribute `plugin_type == 'inventory'` it will be treated
          as an inventory plugin.
        - Otherwise, if the plugin is an instance of `InventoryPluginInterface`
          (from the project's base), it will be treated as inventory.

        Returns a mapping of plugin name -> result or exception info.
        """
        results: Dict[str, Any] = {}
        # import here to avoid circular imports when module is loaded
        try:
            from agentredfish.base.inventory_plugin_base import InventoryPluginInterface
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
                results[name] = {"result": res}
            except Exception as e:
                results[name] = {"error": str(e)}

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
            from agentredfish.base.output_plugin_base import OutputPluginInterface
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