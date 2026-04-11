from engines.base_engine import BaseEngine
from framework.context import TradingContext

class ScannerOnlyEngine(BaseEngine):
    """
    Second Engine: Passive Market Scanner (Framework-Driven).
    Only watches symbols and reports price, NO trading.
    """
    def __init__(self):
        super().__init__("ScannerOnly_V2")
        self.symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'BNB/USDT']

    async def on_start(self, ctx: TradingContext):
        await super().on_start(ctx)
        self.ctx.log("Scanner starting... subscribing to tickers.", "INFO")
        for sym in self.symbols:
            self.ctx.subscribe_ticker(sym)

    async def on_tick(self, symbol: str, ticker: dict):
        if symbol in self.symbols:
            price = float(ticker.get('last', 0))
            self.ctx.report_status(symbol, "SCAN", f"Ticker price: ${price:,.2f}")
