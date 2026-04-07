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
        if not self.is_running: 
            self.current_phase = self.PHASE_IDLE
            return
        
        self.current_phase = self.PHASE_SCAN
        self.status_message = f"Scanning {len(self.symbols)} symbols..."
        
        for symbol in self.symbols:
            if not self.is_running: break
            try:
                self.current_phase = self.PHASE_DATA
                self.status_message = f"Fetching {symbol}..."
                ticker = await market_collector.fetch_ticker(symbol)
                if ticker:
                    self.cached_data[symbol] = {
                        'price': float(ticker['last']),
                        'last_update': time.time()
                    }
                await asyncio.sleep(0.5)
            except Exception as e:
                self.logger.error(f"Scanner error on {symbol}: {e}")

        self.current_phase = self.PHASE_IDLE
        self.status_message = "Scan cycle complete."

    async def get_stats(self) -> Dict[str, Any]:
        return {
            'equity': self.equity,
            'initial_equity': self.equity,
            'total_pnl': 0.0,
            'daily_pnl': 0.0,
            'weekly_pnl': 0.0,
            'monthly_pnl': 0.0,
            'yearly_pnl': 0.0,
            'next_reset_in': '00:00:00',
            'active_pos_count': 0,
            'mode': 'SCAN_ONLY',
            'current_phase': self.current_phase,
            'status_message': self.status_message
        }

    async def get_active_orders(self) -> List[Dict[str, Any]]:
        return []

    async def close_position(self, order_id: str):
        self.logger.info(f"Manual close requested for {order_id} in scanner-only engine.")

    async def shutdown(self):
        self.stop()
        self.logger.info(f"Engine {self.name} shut down cleanly.")
