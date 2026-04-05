from abc import ABC, abstractmethod


class TriggerPluginInterface(ABC):
    @abstractmethod
    def initialize(self, context: dict) -> None:
        """Initialize the plugin.

        ``context`` is a dictionary that must include at minimum:
        - ``llm_service``: the shared :class:`services.llm.LLMService` instance.
        """
        pass

    @abstractmethod
    async def run(self) -> None:
        """Start the trigger loop.  Should run indefinitely until stopped."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Signal the trigger to stop its loop."""
        pass
