from engines.base_engine import BaseEngine
from framework.context import TradingContext

class PlaceholderFuturesEngine(BaseEngine):
    """
    A temporary stub engine for Futures trading.
    """
    def __init__(self):
        super().__init__("FuturesPlaceholder")
        self.symbols_to_monitor = ['BTC/USDT:USDT', 'ETH/USDT:USDT']

    async def on_start(self, ctx: TradingContext):
        await super().on_start(ctx)
        self.ctx.log("Futures placeholder starting...", "INFO")
        for sym in self.symbols_to_monitor:
            self.ctx.subscribe_ticker(sym)

    async def on_tick(self, symbol: str, ticker: dict):
        if symbol in self.symbols_to_monitor:
            price = float(ticker.get('last', 0))
            self.ctx.report_status(symbol, "ANALYZE", f"Futures Price: ${price:,.2f}")
