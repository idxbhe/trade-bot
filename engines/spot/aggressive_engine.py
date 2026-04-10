import asyncio
import time
from typing import Dict, List, Any
from engines.base_engine import BaseEngine
from data.fetcher import market_collector
from data.indicators import Indicators
from strategy.volatility_breakout import VolatilityBreakoutStrategy
from risk.position_sizer import PositionSizer
from risk.circuit_breaker import CircuitBreaker
from execution.order_manager import order_manager

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
        
        # Load persisted positions
        self.active_positions = await self.load_active_positions()
        
        # Aggressive Scalping: 0.5% risk per trade, 1.2x ATR stop loss
        self.risk_manager = PositionSizer(max_risk_pct=0.005, atr_multiplier=1.2)
        # Tight Circuit Breaker: 2% daily drawdown
        self.circuit_breaker = CircuitBreaker(max_daily_drawdown_pct=0.02)
        stored_equity = await self.circuit_breaker.load_baselines(self.name)
        
        if stored_equity is not None:
            self.equity = stored_equity
            self.logger.info(f"[{self.name}] Continued with persisted equity: ${self.equity:,.2f}")
        
        self.logger.info(f"Engine {self.name} initialized. Mode: {'LIVE' if self.is_live else 'TEST'}. Loaded {len(self.active_positions)} active positions.")

    async def update(self):
        if not self.is_running or getattr(self, '_is_updating', False):
            self.current_phase = self.PHASE_IDLE
            return
            
        self._is_updating = True
        try:
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
                            await self._close_position(symbol, price, "TAKE_PROFIT")
                        # 2. Hard SL: 1.0x ATR
                        elif price <= pos['stop_loss']:
                            self.current_phase = self.PHASE_EXEC
                            self.report_execution(symbol, f"TIGHT SL HIT @ ${price:,.2f}")
                            await self._close_position(symbol, price, "STOP_LOSS")
                        # 3. Dynamic TP: Price closed back inside Bollinger Bands
                        elif sig['signal'] == 'EXIT_LONG':
                            self.current_phase = self.PHASE_EXEC
                            self.report_execution(symbol, f"DYNAMIC TP (Fell inside BB) @ ${price:,.2f}")
                            await self._close_position(symbol, price, "DYNAMIC_EXIT")
                    else:
                        self.current_phase = self.PHASE_RISK
                        if symbol in self.cached_data:
                            sig = self.cached_data[symbol]['signal']
                            if sig['signal'] == 'BUY':
                                self.report_risk(symbol, "Validating fast scalp risk...")
                                if self.circuit_breaker.update_equity(self.get_total_equity(), self.name):
                                    self.current_phase = self.PHASE_EXEC
                                    atr = self.cached_data[symbol]['df'].iloc[-1].get('atr_14', 0)
                                    if atr > 0:
                                        # Tight SL: 1x ATR, TP: 1.5x ATR
                                        sl = price - (1.0 * atr)
                                        tp = price + (1.5 * atr)
                                        # Using position_sizer to respect max risk
                                        size, _ = self.risk_manager.calculate_position_size(self.equity, price, sl)
                                        if size > 0:
                                            self.report_execution(symbol, f"EXEC VOLATILITY SCALP {size:.4f} @ ${price:,.2f}")
                                            await self._open_position(symbol, price, size, sl, tp)
                                            
                    self.current_phase = self.PHASE_SYNC
                    await asyncio.sleep(0.5) # Extremely fast loop
                    
                except Exception as e:
                    self.logger.error(f"Error in Aggressive Engine for {symbol}: {e}")

            # Save baselines
            await self.circuit_breaker.save_baselines(self.name, self.equity)
            self.current_phase = self.PHASE_IDLE
        finally:
            self._is_updating = False

    async def _open_position(self, symbol: str, price: float, amount: float, sl: float, tp: float):
        if self.is_live:
            self.report_execution(symbol, f"Executing LIVE BUY {amount:.4f} @ ${price:,.2f}")
            order = await order_manager.execute_limit_order(symbol, 'buy', amount, price)
            if not order:
                self.report_info(f"[{symbol}] LIVE order failed. Skipping position.")
                return

        cost = price * amount
        self.equity -= cost
        pos = {
            'entry_price': price,
            'amount': amount,
            'stop_loss': sl,
            'take_profit': tp,
            'max_pnl': 0.0,
            'min_pnl': 0.0
        }
        self.active_positions[symbol] = pos
        await self.save_active_position(symbol, pos, side="LONG")
        self.report_info(f"[{symbol}] OPEN LONG {amount:.4f} @ ${price:,.2f} | SL: ${sl:,.2f} | TP: ${tp:,.2f}")

    async def _close_position(self, symbol: str, price: float, reason: str):
        pos = self.active_positions.pop(symbol)
        await self.remove_active_position(symbol)

        if self.is_live:
            self.report_execution(symbol, f"Executing LIVE SELL {pos['amount']:.4f} @ ${price:,.2f}")
            await order_manager.execute_limit_order(symbol, 'sell', pos['amount'], price)

        revenue = price * pos['amount']
        self.equity += revenue
        pnl = revenue - (pos['entry_price'] * pos['amount'])
        self.report_info(f"[{symbol}] CLOSE LONG ({reason}) @ ${price:,.2f} | PnL: ${pnl:,.2f}")

        # Record history
        record = {
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
        }
        await self.save_order_history(record)

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
        hist = await self.get_historical_stats()
        
        # UI baseline stats (reset timer)
        if self.circuit_breaker:
            pnl_stats = self.circuit_breaker.get_pnl_stats(total_equity)
            daily_pnl = pnl_stats['daily_pnl']
            weekly_pnl = pnl_stats['weekly_pnl']
            monthly_pnl = pnl_stats['monthly_pnl']
            yearly_pnl = pnl_stats['yearly_pnl']
            next_reset_in = pnl_stats['next_reset_in']
        else:
            daily_pnl = weekly_pnl = monthly_pnl = yearly_pnl = 0.0
            next_reset_in = '00:00:00'
        
        # Total PnL is Lifetime Realized + Current Floating
        active_orders = await self.get_active_orders()
        floating_pnl = sum(order['pnl'] for order in active_orders)
        total_pnl = hist['historical_pnl'] + floating_pnl
        
        return {
            'equity': total_equity,
            'initial_equity': self.initial_equity,
            'total_pnl': total_pnl,
            'win_rate': hist['win_rate'],
            'trade_count': hist['trade_count'],
            'daily_pnl': daily_pnl,
            'weekly_pnl': weekly_pnl,
            'monthly_pnl': monthly_pnl,
            'yearly_pnl': yearly_pnl,
            'next_reset_in': next_reset_in,
            'active_pos_count': len(self.active_positions),
            'mode': self.mode,
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
            await self._close_position(symbol, price, "MANUAL_CLOSE")

    async def shutdown(self):
        self.stop()
        self.logger.info(f"Engine {self.name} shutting down.")
