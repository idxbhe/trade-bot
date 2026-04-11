from typing import Dict, List, Optional, Any
import pandas as pd

class TradingContext:
    """
    Dependency Injection API for Engines.
    Provides all data, state, and execution capabilities cleanly.
    Engine developers only interact with this object, keeping engines pure.
    """
    def __init__(self, kernel, engine_name: str, mode: str, market: str):
        self._kernel = kernel
        self.engine_name = engine_name
        self.mode = mode
        self.market = market

    # --- Data Subscriptions ---
    def subscribe_ticker(self, symbol: str):
        """Ask the kernel to push ticker updates to this engine via on_tick()."""
        self._kernel.subscribe_ticker(self.engine_name, symbol)

    def subscribe_candles(self, symbol: str, timeframe: str):
        """Ask the kernel to push candle updates to this engine via on_candle_closed()."""
        self._kernel.subscribe_candles(self.engine_name, symbol, timeframe)

    async def get_historical_data(self, symbol: str, timeframe: str = '1m', limit: int = 50) -> pd.DataFrame:
        """Pull historical data synchronously for initialization or heavy analysis."""
        return await self._kernel.get_historical_data(symbol, timeframe, limit)

    async def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        """Fetch the current order book for a symbol."""
        return await self._kernel.get_order_book(self.engine_name, symbol, limit)

    # --- State Management ---
    def get_equity(self) -> float:
        """Returns the current wallet equity for this engine from memory."""
        return self._kernel.state_manager.get_equity(self.engine_name)

    def get_baseline(self, timeframe: str = 'daily') -> float:
        """Returns the reset baseline for the specified timeframe (e.g. 'daily', 'weekly')."""
        return self._kernel.state_manager.get_baseline(self.engine_name, timeframe)

    def get_position(self, symbol: str) -> Optional[dict]:
        """Returns active position data for a symbol if it exists."""
        return self._kernel.state_manager.get_position(self.engine_name, symbol)

    def get_all_positions(self) -> Dict[str, dict]:
        """Returns all active positions for this engine."""
        return self._kernel.state_manager.get_all_positions(self.engine_name)

    # --- Execution ---
    async def buy_limit(self, symbol: str, amount: float, price: float, sl: float = 0.0, tp: float = 0.0, post_only: bool = True) -> bool:
        """Request the kernel to place a buy limit order."""
        return await self._kernel.place_order(self.engine_name, symbol, 'BUY', amount, price, sl, tp, post_only)

    async def sell_limit(self, symbol: str, amount: float, price: float, sl: float = 0.0, tp: float = 0.0, post_only: bool = True) -> bool:
        """Request the kernel to place a sell limit order."""
        return await self._kernel.place_order(self.engine_name, symbol, 'SELL', amount, price, sl, tp, post_only)

    async def close_position(self, symbol: str, exit_price: float, reason: str) -> bool:
        """Request the kernel to close an active position immediately."""
        return await self._kernel.close_position(self.engine_name, symbol, exit_price, reason)

    # --- UI & Logging ---
    def log(self, message: str, level: str = "INFO"):
        """Send a message to the system logger and the UI log modal."""
        self._kernel.log(self.engine_name, message, level)

    def report_status(self, symbol: str, phase: str, message: str):
        """
        Update the UI status pipeline (SCAN, ANALYZE, EXEC, etc.)
        Phase should be one of: SCAN, DATA, ANALYZE, RISK, EXEC, SYNC, IDLE.
        """
        self._kernel.report_status(self.engine_name, symbol, phase, message)
