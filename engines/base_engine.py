from abc import ABC, abstractmethod
from typing import Dict, List, Any
from core.logger import logger

class BaseEngine(ABC):
    """
    Abstract Base Class for all Trading Engines (Plug-and-Play).
    Each engine must implement these methods to be compatible with the Dashboard.
    """
    # Standard Transaction Phases
    PHASE_IDLE = "IDLE"
    PHASE_SCAN = "SCAN"
    PHASE_DATA = "DATA"
    PHASE_ANALYZE = "ANALYZE"
    PHASE_RISK = "RISK"
    PHASE_EXEC = "EXEC"
    PHASE_SYNC = "SYNC"

    def __init__(self, name: str):
        self.name = name
        self.is_running = False
        self.exchange = None
        self.config = None
        self.logger = logger
        self.current_phase = self.PHASE_IDLE
        self.status_message = "Engine initialized"
        self.verbose_queue = [] # Pretty logs for the UI
        self.latest_activity = "Engine ready" # Static status line

    def _push_log(self, category: str, icon: str, color: str, symbol: str, message: str):
        """Standardized log formatter for UI consistency."""
        # Update current state for the static UI
        self.current_phase = category
        
        # Format the static line: [CATEGORY] Symbol Message (But hide SCAN as requested)
        if category == "SCAN":
            self.latest_activity = f"{symbol:10} {message}"
        else:
            self.latest_activity = f"[{category:8}] {symbol:10} {message}"

        # Push to queue for the bottom history log (removing timestamps)
        formatted = f"[{color}]{icon} {category:8}[/] [bold]{symbol:10}[/] {message}"
        self.verbose_queue.append(formatted)

    def report_scan(self, symbol: str, msg: str = "Scanning..."):
        self._push_log("SCAN", "🔍", "yellow", symbol, msg)

    def report_analyze(self, symbol: str, msg: str):
        self._push_log("ANALYZE", "📊", "magenta", symbol, msg)

    def report_risk(self, symbol: str, msg: str):
        self._push_log("RISK", "🛡️", "orange3", symbol, msg)

    def report_execution(self, symbol: str, msg: str):
        self._push_log("EXEC", "🚀", "bold green", symbol, msg)

    def report_info(self, msg: str):
        self.latest_activity = msg
        self.verbose_queue.append(f"[blue]ℹ INFO[/] {msg}")

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
    async def get_active_orders(self) -> List[Dict[str, Any]]:
        """Return list of active orders/positions for the UI."""
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
