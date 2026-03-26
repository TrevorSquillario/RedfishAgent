from abc import ABC, abstractmethod

class OutputPluginInterface(ABC):
    @abstractmethod
    def initialize(self):
        """Initialize the plugin"""
        pass

    @abstractmethod
    def send(self, input_data: str):
        """Execute the main logic"""
        pass