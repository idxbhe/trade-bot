import asyncio
import time
from typing import Dict, List, Any
from engines.base_engine import BaseEngine
from data.fetcher import market_collector
from data.indicators import Indicators
from strategy.volatility_breakout import VolatilityBreakoutStrategy
from risk.position_sizer import position_sizer
from risk.circuit_breaker import circuit_breaker

class AggressiveScalperEngine(BaseEngine):
    """
    High-Frequency Scalping Engine (1-minute timeframe).
    Focuses on highly liquid pairs to catch momentum breakouts with very tight risk.
    """

    def __init__(self):
        super().__init__("AggressiveScalper")
        self.strategy = VolatilityBreakoutStrategy()
        # High liquidity pairs to avoid the "Fee Trap" and Slippage
        self.symbols_to_monitor = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
        self.active_positions = {}
        self.cached_data = {}
        
        from core.config import config
        self.equity = config.TEST_INITIAL_BALANCE
        self.initial_equity = config.TEST_INITIAL_BALANCE
        self.is_live = config.KUCOIN_ENV.lower() != 'sandbox'

    async def setup(self, exchange_client: Any, config: Any):
        self.exchange = exchange_client
        self.config = config
        self.equity = config.TEST_INITIAL_BALANCE
        self.initial_equity = config.TEST_INITIAL_BALANCE
        self.is_live = config.KUCOIN_ENV.lower() != 'sandbox'
        self.logger.info(f"Engine {self.name} initialized. Mode: {'LIVE' if self.is_live else 'TEST'}")

    async def update(self):
        if not self.is_running:
            self.current_phase = self.PHASE_IDLE
            return
            
        self.current_phase = self.PHASE_SCAN
        self.report_info(f"Rapid scanning {len(self.symbols_to_monitor)} pairs on 1m timeframe...")
        
        for symbol in self.symbols_to_monitor:
            if not self.is_running: break
            try:
                now = time.time()
                # Update candles more frequently for 1m strategy (e.g., every 15 seconds)
                needs_update = symbol not in self.cached_data or (now - self.cached_data[symbol].get('last_update', 0)) > 15

                if not needs_update and symbol not in self.active_positions:
                    await asyncio.sleep(0.1) # Small delay for yielding
                    continue

                self.current_phase = self.PHASE_DATA
                self.report_scan(symbol, "Fetching fast 1m ticker...")

                ticker = await market_collector.fetch_ticker(symbol)
                if not ticker:
                    continue

                price = float(ticker['last'])

                if needs_update:
                    self.report_scan(symbol, "Fetching 1m candles...")
                    df = await market_collector.get_historical_data(symbol, timeframe='1m', limit=50)
                    if not df.empty:
                        self.current_phase = self.PHASE_ANALYZE
                        df = Indicators.attach_all_indicators(df)
                        # Add custom vol_sma
                        df['vol_sma_20'] = Indicators.calculate_sma(df, period=20, column='volume')
                        
                        signal = self.strategy.generate_signal(df, price)
                        self.cached_data[symbol] = {
                            'df': df,
                            'rsi': df.iloc[-1].get('rsi_14', 0),
                            'adx': df.iloc[-1].get('adx_14', 0),
                            'signal': signal,
                            'last_update': now
                        }
                        if signal['signal'] != 'HOLD':
                            self.report_analyze(symbol, f"Breakout logic triggered: [bold]{signal['signal']}[/]")

                if symbol in self.active_positions:
                    self.current_phase = self.PHASE_RISK
                    pos = self.active_positions[symbol]
                    sig = self.cached_data[symbol]['signal']
                    
                    # Update Max/Min Floating PnL
                    floating_pnl = (price - pos['entry_price']) * pos['amount']
                    pos['max_pnl'] = max(pos.get('max_pnl', 0.0), floating_pnl)
                    pos['min_pnl'] = min(pos.get('min_pnl', 0.0), floating_pnl)
                    
                    # 1. Fixed TP: 1.5x ATR
                    if price >= pos['take_profit']:
                        self.current_phase = self.PHASE_EXEC
                        self.report_execution(symbol, f"FIXED TP HIT (1.5x ATR) @ ${price:,.2f}")
                        self._close_position(symbol, price, "TAKE_PROFIT")
                    # 2. Hard SL: 1.0x ATR
                    elif price <= pos['stop_loss']:
                        self.current_phase = self.PHASE_EXEC
                        self.report_execution(symbol, f"TIGHT SL HIT @ ${price:,.2f}")
                        self._close_position(symbol, price, "STOP_LOSS")
                    # 3. Dynamic TP: Price closed back inside Bollinger Bands
                    elif sig['signal'] == 'EXIT_LONG':
                        self.current_phase = self.PHASE_EXEC
                        self.report_execution(symbol, f"DYNAMIC TP (Fell inside BB) @ ${price:,.2f}")
                        self._close_position(symbol, price, "DYNAMIC_EXIT")
                else:
                    self.current_phase = self.PHASE_RISK
                    if symbol in self.cached_data:
                        sig = self.cached_data[symbol]['signal']
                        if sig['signal'] == 'BUY':
                            self.report_risk(symbol, "Validating fast scalp risk...")
                            if circuit_breaker.update_equity(self.get_total_equity()):
                                self.current_phase = self.PHASE_EXEC
                                atr = self.cached_data[symbol]['df'].iloc[-1].get('atr_14', 0)
                                if atr > 0:
                                    # Tight SL: 1x ATR, TP: 1.5x ATR
                                    sl = price - (1.0 * atr)
                                    tp = price + (1.5 * atr)
                                    # Using position_sizer to respect max risk
                                    size, _ = position_sizer.calculate_position_size(self.equity, price, sl)
                                    if size > 0:
                                        self.report_execution(symbol, f"EXEC VOLATILITY SCALP {size:.4f} @ ${price:,.2f}")
                                        self._open_position(symbol, price, size, sl, tp)
                                        
                self.current_phase = self.PHASE_SYNC
                await asyncio.sleep(0.5) # Extremely fast loop
                
            except Exception as e:
                self.logger.error(f"Error in Aggressive Engine for {symbol}: {e}")

        self.current_phase = self.PHASE_IDLE

    def _open_position(self, symbol: str, price: float, amount: float, sl: float, tp: float):
        cost = price * amount
        self.equity -= cost
        self.active_positions[symbol] = {
            'entry_price': price,
            'amount': amount,
            'stop_loss': sl,
            'take_profit': tp,
            'max_pnl': 0.0,
            'min_pnl': 0.0
        }
        self.report_info(f"[{symbol}] OPEN LONG {amount:.4f} @ ${price:,.2f} | SL: ${sl:,.2f} | TP: ${tp:,.2f}")

    def _close_position(self, symbol: str, price: float, reason: str):
        pos = self.active_positions.pop(symbol)
        revenue = price * pos['amount']
        self.equity += revenue
        pnl = revenue - (pos['entry_price'] * pos['amount'])
        self.report_info(f"[{symbol}] CLOSE LONG ({reason}) @ ${price:,.2f} | PnL: ${pnl:,.2f}")

        # Record history
        self.order_history.append({
            'time': time.strftime("%H:%M:%S"),
            'symbol': symbol,
            'side': 'LONG',
            'amount': pos['amount'],
            'entry': pos['entry_price'],
            'exit': price,
            'pnl': pnl,
            'max_pnl': pos.get('max_pnl', 0.0),
            'min_pnl': pos.get('min_pnl', 0.0),
            'reason': reason
        })

    def get_total_equity(self) -> float:
        total_equity = self.equity
        for sym, pos in self.active_positions.items():
            if sym in self.cached_data:
                current_price = self.cached_data[sym]['df'].iloc[-1]['close']
                total_equity += current_price * pos['amount']
            else:
                total_equity += pos['entry_price'] * pos['amount']
        return total_equity

    async def get_stats(self) -> Dict[str, Any]:
        total_equity = self.get_total_equity()
        pnl_stats = circuit_breaker.get_pnl_stats(total_equity)
        
        return {
            'equity': total_equity,
            'initial_equity': self.initial_equity,
            'total_pnl': total_equity - self.initial_equity,
            'daily_pnl': pnl_stats['daily_pnl'],
            'weekly_pnl': pnl_stats['weekly_pnl'],
            'monthly_pnl': pnl_stats['monthly_pnl'],
            'yearly_pnl': pnl_stats['yearly_pnl'],
            'next_reset_in': pnl_stats['next_reset_in'],
            'active_pos_count': len(self.active_positions),
            'mode': 'LIVE' if self.is_live else 'TEST',
            'current_phase': self.current_phase,
            'status_message': self.status_message
        }

    async def get_active_orders(self) -> List[Dict[str, Any]]:
        results = []
        for symbol, pos in self.active_positions.items():
            current_price = pos['entry_price']
            if symbol in self.cached_data:
                current_price = self.cached_data[symbol]['df'].iloc[-1]['close']
                
            pnl_val = (current_price - pos['entry_price']) * pos['amount']
            
            results.append({
                'order_id': f"{symbol}_LONG",
                'symbol': symbol,
                'side': 'LONG',
                'size': pos['amount'],
                'entry': pos['entry_price'],
                'current': current_price,
                'sl': pos.get('stop_loss', 0.0),
                'tp': pos.get('take_profit', 0.0),
                'pnl': pnl_val
            })
        return results

    async def close_position(self, order_id: str):
        # order_id: BTC/USDT_LONG
        symbol = order_id.rsplit('_', 1)[0]
        if symbol in self.active_positions:
            price = self.active_positions[symbol]['entry_price']
            if symbol in self.cached_data:
                price = self.cached_data[symbol]['df'].iloc[-1]['close']
            self._close_position(symbol, price, "MANUAL_CLOSE")

    async def shutdown(self):
        self.stop()
        self.logger.info(f"Engine {self.name} shutting down.")
