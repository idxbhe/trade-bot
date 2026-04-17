from abc import ABC
from typing import Any
from framework.context import TradingContext

class BaseEngine(ABC):
    """
    Abstract Base Class for all Framework-driven Trading Engines.
    Engines are now purely functional: they receive data and use the TradingContext to execute actions.
    No direct database or exchange API calls are permitted here.
    """
    def __init__(self, name: str):
        self.name = name
        self.ctx: TradingContext = None

    async def on_start(self, ctx: TradingContext):
        """Called once when the engine is registered with the Kernel."""
        self.ctx = ctx

    async def on_tick(self, symbol: str, ticker: dict):
        """Called by the DataStream when a new price update arrives."""
        pass

    async def on_candle_closed(self, symbol: str, df: Any):
        """Called when a new candle closes."""
        pass
    
    async def on_price_update(self, symbol: str, price: float, stats: dict):
        """Optional hook for lightweight ticker processing."""
        pass

    async def shutdown(self):
        """Called when the Kernel stops."""
        if self.ctx:
            self.ctx.log("Engine shutting down cleanly.", "INFO")
