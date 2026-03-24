from abc import ABC, abstractmethod
from typing import List

from models.plugins import InventoryEntry


class InventoryPluginInterface(ABC):
    @abstractmethod
    def initialize(self) -> None:
        """Initialize the plugin"""
        raise NotImplementedError()

    @abstractmethod
    def load(self) -> List[InventoryEntry]:
        """Execute the main logic and return normalized inventory entries."""
        raise NotImplementedError()