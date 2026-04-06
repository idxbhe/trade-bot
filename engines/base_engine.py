from abc import ABC, abstractmethod
from typing import Dict, List, Any
from core.logger import logger

class BaseEngine(ABC):
    """
    Abstract Base Class for all Trading Engines (Plug-and-Play).
    Each engine must implement these methods to be compatible with the Dashboard.
    """

    def __init__(self, name: str):
        self.name = name
        self.is_running = False
        self.exchange = None
        self.config = None
        self.logger = logger

    @abstractmethod
    async def setup(self, exchange_client: Any, config: Any):
        """Initialize the engine with resources and settings."""
        pass

    @abstractmethod
    async def update(self):
        """
        The main 'heartbeat' of the engine.
        Called periodically to perform scanning, filtering, and execution.
        """
        pass

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        """Return performance and account status data for the UI."""
        pass

    @abstractmethod
    async def get_active_symbols(self) -> List[Dict[str, Any]]:
        """Return list of symbols being monitored/traded for the UI."""
        pass

    @abstractmethod
    async def shutdown(self):
        """Safely close positions or cancel orders before stopping."""
        pass

    def start(self):
        """Generic start flag."""
        self.is_running = True
        self.logger.info(f"Engine '{self.name}' started.")

    def stop(self):
        """Generic stop flag."""
        self.is_running = False
        self.logger.info(f"Engine '{self.name}' stopped.")
