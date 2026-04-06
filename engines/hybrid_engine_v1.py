import asyncio
import time
from typing import Dict, List, Any
from engines.base_engine import BaseEngine
from data.fetcher import market_collector
from data.indicators import Indicators
from strategy.mean_reversion import MeanReversionStrategy
from risk.position_sizer import position_sizer
from risk.circuit_breaker import circuit_breaker

class HybridEngineV1(BaseEngine):
    """
    First Autonomous Engine: Hybrid Mean Reversion + Scanning.
    Scans for top volume pairs and trades them using Mean Reversion.
    """

    def __init__(self):
        super().__init__("HybridEngine_V1")
        self.strategy = MeanReversionStrategy()
        self.symbols_to_monitor = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT']
        self.active_positions = {}
        self.cached_data = {}
        # Pre-initialize with config values so UI doesn't show 0 at start
        from core.config import config
        self.equity = config.TEST_INITIAL_BALANCE
        self.initial_equity = config.TEST_INITIAL_BALANCE
        self.is_live = config.KUCOIN_ENV.lower() != 'sandbox'
        self.last_scan_time = 0
        self.SCAN_INTERVAL = 300 # Scan for new pairs every 5 mins

    async def setup(self, exchange_client: Any, config: Any):
        self.exchange = exchange_client
        self.config = config
        self.equity = config.TEST_INITIAL_BALANCE
        self.initial_equity = config.TEST_INITIAL_BALANCE
        self.is_live = config.KUCOIN_ENV.lower() != 'sandbox'
        self.logger.info(f"Engine {self.name} initialized. Mode: {'LIVE' if self.is_live else 'TEST'}")

    async def update(self):
        """The core tick of the hybrid engine."""
        if not self.is_running: return
        
        # 1. Dynamic Market Scanning (Placeholder for now, uses target_symbols)
        # In a real scanner, we would fetch top gainers/losers or top volume pairs here.
        
        for symbol in self.symbols_to_monitor:
            try:
                # Lightweight Update: Ticker
                ticker = await market_collector.fetch_ticker(symbol)
                if not ticker: continue
                
                price = float(ticker['last'])
                
                # Heavyweight Update: OHLCV & Indicators (only if cache expired)
                now = time.time()
                needs_update = symbol not in self.cached_data or (now - self.cached_data[symbol].get('last_update', 0)) > 60
                
                if needs_update:
                    df = await market_collector.get_historical_data(symbol, limit=50)
                    if not df.empty:
                        df = Indicators.attach_all_indicators(df)
                        signal = self.strategy.generate_signal(df, price)
                        self.cached_data[symbol] = {
                            'df': df,
                            'rsi': df.iloc[-1]['rsi_14'],
                            'adx': df.iloc[-1]['adx_14'],
                            'signal': signal,
                            'last_update': now
                        }

                # Execution Logic
                if symbol in self.active_positions:
                    # Manage existing position (SL/TP/Exit)
                    pos = self.active_positions[symbol]
                    sig = self.cached_data[symbol]['signal']
                    
                    if price <= pos['stop_loss']:
                        self.logger.warning(f"[{symbol}] STOP LOSS HIT @ ${price:,.2f}")
                        self._close_position(symbol, price, "STOP_LOSS")
                    elif sig['signal'] == 'EXIT_LONG':
                        self.logger.info(f"[{symbol}] EXIT SIGNAL @ ${price:,.2f}")
                        self._close_position(symbol, price, "SIGNAL_EXIT")
                else:
                    # Look for new entries
                    sig = self.cached_data[symbol]['signal']
                    if sig['signal'] == 'BUY':
                        if circuit_breaker.update_equity(self.equity):
                            atr = self.cached_data[symbol]['df'].iloc[-1].get('atr_14', 0)
                            sl = position_sizer.calculate_stop_loss(price, atr)
                            size, _ = position_sizer.calculate_position_size(self.equity, price, sl)
                            
                            if size > 0:
                                self._open_position(symbol, price, size, sl)
                
                # Mandatory spacing between symbols
                await asyncio.sleep(1.0)
                
            except Exception as e:
                self.logger.error(f"Error updating {symbol} in {self.name}: {e}")

    def _open_position(self, symbol: str, price: float, size: float, sl: float):
        cost = size * price
        if cost <= self.equity:
            self.equity -= cost
            self.active_positions[symbol] = {
                'amount': size,
                'entry_price': price,
                'stop_loss': sl,
                'entry_time': time.time()
            }
            self.logger.info(f"[{symbol}] PAPER BUY {size:.4f} @ ${price:,.2f} | SL: ${sl:,.2f}")

    def _close_position(self, symbol: str, price: float, reason: str):
        pos = self.active_positions[symbol]
        revenue = pos['amount'] * price
        pnl = revenue - (pos['entry_price'] * pos['amount'])
        self.equity += revenue
        del self.active_positions[symbol]
        self.logger.info(f"[{symbol}] PAPER {reason} @ ${price:,.2f} | PNL: ${pnl:,.2f} ({pnl/ (pos['entry_price']*pos['amount'])*100:.2f}%)")

    async def get_stats(self) -> Dict[str, Any]:
        return {
            'equity': self.equity,
            'initial_equity': self.initial_equity,
            'total_pnl': self.equity - self.initial_equity,
            'active_pos_count': len(self.active_positions),
            'mode': 'LIVE' if self.is_live else 'TEST'
        }

    async def get_active_symbols(self) -> List[Dict[str, Any]]:
        results = []
        for symbol, data in self.cached_data.items():
            price = data['df'].iloc[-1]['close'] # Default to last close if no ticker
            pos_info = "None"
            pnl_info = "$0.00"
            
            if symbol in self.active_positions:
                pos = self.active_positions[symbol]
                pos_info = f"LONG {pos['amount']:.4f}"
                pnl_val = (price - pos['entry_price']) * pos['amount']
                pnl_info = f"{'+' if pnl_val >= 0 else '-'}${abs(pnl_val):,.2f}"
                
            results.append({
                'symbol': symbol,
                'price': price,
                'rsi': data['rsi'],
                'adx': data['adx'],
                'signal': data['signal']['signal'],
                'position': pos_info,
                'pnl': pnl_info
            })
        return results

    async def shutdown(self):
        self.stop()
        self.logger.info(f"Engine {self.name} shutting down. Cleaning up positions...")
        # Optional: In a real bot, we might cancel open limit orders here.
