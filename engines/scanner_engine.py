import asyncio
import time
from typing import Dict, List, Any
from engines.base_engine import BaseEngine
from data.fetcher import market_collector

class ScannerOnlyEngine(BaseEngine):
    """
    Second Engine: Passive Market Scanner.
    Only watches symbols and reports price, NO trading.
    Useful for testing Plug-and-Play switching.
    """

    def __init__(self):
        super().__init__("ScannerOnly_V2")
        self.symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'BNB/USDT']
        self.cached_data = {}
        self.equity = 10000

    async def setup(self, exchange_client: Any, config: Any):
        self.exchange = exchange_client
        self.logger.info(f"Engine {self.name} initialized for passive monitoring.")

    async def update(self):
        if not self.is_running: return
        
        for symbol in self.symbols:
            try:
                ticker = await market_collector.fetch_ticker(symbol)
                if ticker:
                    self.cached_data[symbol] = {
                        'price': float(ticker['last']),
                        'last_update': time.time()
                    }
                await asyncio.sleep(0.5)
            except Exception as e:
                self.logger.error(f"Scanner error on {symbol}: {e}")

    async def get_stats(self) -> Dict[str, Any]:
        return {
            'equity': self.equity,
            'mode': 'SCAN_ONLY',
            'active_pos_count': 0,
            'total_pnl': 0
        }

    async def get_active_symbols(self) -> List[Dict[str, Any]]:
        results = []
        for sym, data in self.cached_data.items():
            results.append({
                'symbol': sym,
                'price': data['price'],
                'rsi': 0.0,
                'adx': 0.0,
                'signal': 'SCANNING',
                'position': 'None',
                'pnl': '$0.00'
            })
        return results

    async def shutdown(self):
        self.stop()
        self.logger.info(f"Engine {self.name} shut down cleanly.")
