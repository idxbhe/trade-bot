import asyncio
from typing import Dict, List, Any
from engines.base_engine import BaseEngine

class PlaceholderFuturesEngine(BaseEngine):
    """
    A temporary stub engine for Futures trading.
    Used to validate the UI and KuCoin Futures client integration.
    """
    def __init__(self):
        super().__init__("FuturesPlaceholder")
        self.symbols_to_monitor = ['BTC/USDT:USDT', 'ETH/USDT:USDT']
        self.equity = 1000.0

    async def setup(self, exchange_client: Any, config: Any):
        self.exchange = exchange_client
        self.config = config
        self.logger.info(f"Engine {self.name} initialized with Futures Client.")

    async def update(self):
        if not self.is_running:
            self.current_phase = self.PHASE_IDLE
            return
            
        self.current_phase = self.PHASE_SCAN
        self.report_info("Scanning futures pairs...")
        
        for symbol in self.symbols_to_monitor:
            try:
                self.report_scan(symbol, "Fetching futures ticker...")
                # We use the futures exchange client provided in setup
                ticker = await self.exchange.fetch_ticker(symbol)
                if ticker:
                    price = ticker.get('last', 0)
                    self.report_analyze(symbol, f"Futures Price: ${price:,.2f}")
                await asyncio.sleep(1.0)
            except Exception as e:
                self.logger.error(f"Error fetching futures data for {symbol}: {e}")
                
        self.report_info("Futures cycle complete.")
        self.current_phase = self.PHASE_IDLE

    async def get_stats(self) -> Dict[str, Any]:
        return {
            'equity': self.equity,
            'initial_equity': 1000.0,
            'total_pnl': 0.0,
            'daily_pnl': 0.0,
            'weekly_pnl': 0.0,
            'monthly_pnl': 0.0,
            'yearly_pnl': 0.0,
            'next_reset_in': '00:00:00',
            'active_pos_count': 0,
            'mode': 'TEST',
            'current_phase': self.current_phase,
            'status_message': self.status_message
        }

    async def get_active_orders(self) -> List[Dict[str, Any]]:
        return []

    async def shutdown(self):
        self.stop()
        self.logger.info(f"Engine {self.name} shutting down.")
