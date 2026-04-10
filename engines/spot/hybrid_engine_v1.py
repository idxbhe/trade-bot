import asyncio
import time
from typing import Dict, List, Any
from engines.base_engine import BaseEngine
from data.fetcher import market_collector
from data.indicators import Indicators
from strategy.mean_reversion import MeanReversionStrategy
from risk.position_sizer import PositionSizer
from risk.circuit_breaker import CircuitBreaker
from execution.order_manager import order_manager

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
        
        # Load persisted positions
        self.active_positions = await self.load_active_positions()
        
        # Hybrid Mean Reversion: 1.5% risk per trade, 2.5x ATR stop loss
        self.risk_manager = PositionSizer(max_risk_pct=0.015, atr_multiplier=2.5)
        # Standard Circuit Breaker: 5% daily drawdown
        self.circuit_breaker = CircuitBreaker(max_daily_drawdown_pct=0.05)
        await self.circuit_breaker.load_baselines(self.name)
        
        self.logger.info(f"Engine {self.name} initialized. Mode: {'LIVE' if self.is_live else 'TEST'}. Loaded {len(self.active_positions)} active positions.")



    async def update(self):
        """The core tick of the hybrid engine with standardized verbose reporting."""
        if not self.is_running or getattr(self, '_is_updating', False): 
            self.current_phase = self.PHASE_IDLE
            return
            
        self._is_updating = True
        try:
            self.current_phase = self.PHASE_SCAN
            self.report_info(f"Starting cycle for {len(self.symbols_to_monitor)} symbols...")
            
            for symbol in self.symbols_to_monitor:
                if not self.is_running: break
                try:
                    now = time.time()
                    needs_update = symbol not in self.cached_data or (now - self.cached_data[symbol].get('last_update', 0)) > 60
                    
                    if not needs_update and symbol not in self.active_positions:
                        await asyncio.sleep(0.1) # Small delay for yielding
                        continue
                        
                    self.current_phase = self.PHASE_DATA
                    self.report_scan(symbol, "Fetching ticker & data...")
                    
                    ticker = await market_collector.fetch_ticker(symbol)
                    if not ticker: 
                        self.report_info(f"[{symbol}] Failed to fetch ticker, skipping.")
                        continue
                    
                    price = float(ticker['last'])
                    
                    if needs_update:
                        self.report_scan(symbol, "Fetching OHLCV candles...")
                        df = await market_collector.get_historical_data(symbol, limit=50)
                        if not df.empty:
                            self.current_phase = self.PHASE_ANALYZE
                            self.report_analyze(symbol, "Calculating RSI, BB, and ADX...")
                            df = Indicators.attach_all_indicators(df)
                            signal = self.strategy.generate_signal(df, price)
                            self.cached_data[symbol] = {
                                'df': df,
                                'rsi': df.iloc[-1]['rsi_14'],
                                'adx': df.iloc[-1]['adx_14'],
                                'signal': signal,
                                'last_update': now
                            }
                            self.report_analyze(symbol, f"Analysis complete. Signal: [bold]{signal['signal']}[/]")

                    # Execution Logic
                    if symbol in self.active_positions:
                        self.current_phase = self.PHASE_RISK
                        self.report_risk(symbol, "Monitoring active position...")
                        pos = self.active_positions[symbol]
                        sig = self.cached_data[symbol]['signal']
                        
                        # Update Max/Min Floating PnL
                        floating_pnl = (price - pos['entry_price']) * pos['amount']
                        pos['max_pnl'] = max(pos.get('max_pnl', 0.0), floating_pnl)
                        pos['min_pnl'] = min(pos.get('min_pnl', 0.0), floating_pnl)
                        
                        if price <= pos['stop_loss']:
                            self.current_phase = self.PHASE_EXEC
                            self.report_execution(symbol, f"STOP LOSS HIT @ ${price:,.2f}")
                            await self._close_position(symbol, price, "STOP_LOSS")
                        elif sig['signal'] == 'EXIT_LONG':
                            self.current_phase = self.PHASE_EXEC
                            self.report_execution(symbol, f"TAKE PROFIT / EXIT SIGNAL @ ${price:,.2f}")
                            await self._close_position(symbol, price, "SIGNAL_EXIT")
                    else:
                        self.current_phase = self.PHASE_RISK
                        sig = self.cached_data[symbol]['signal']
                        if sig['signal'] == 'BUY':
                            self.report_risk(symbol, "Validating account risk...")
                            if self.circuit_breaker.update_equity(self.get_total_equity(), self.name):
                                self.current_phase = self.PHASE_EXEC
                                atr = self.cached_data[symbol]['df'].iloc[-1].get('atr_14', 0)
                                sl = self.risk_manager.calculate_stop_loss(price, atr)
                                size, _ = self.risk_manager.calculate_position_size(self.equity, price, sl)
                                if size > 0:
                                    if self.is_live:
                                        self.report_execution(symbol, f"Executing LIVE BUY {size:.4f} @ ${price:,.2f}")
                                        order = await order_manager.execute_limit_order(symbol, 'buy', size, price)
                                        if order:
                                            await self._open_position(symbol, price, size, sl)
                                        else:
                                            self.report_info(f"[{symbol}] LIVE order failed. Skipping position.")
                                    else:
                                        self.report_execution(symbol, f"Executing PAPER BUY {size:.4f} @ ${price:,.2f}")
                                        await self._open_position(symbol, price, size, sl)

                    self.current_phase = self.PHASE_SYNC
                    await asyncio.sleep(1.0) # Wait between symbols

                except Exception as e:
                    self.logger.error(f"Error updating {symbol}: {e}")

            # 3. Always save baselines if updated/reset during cycle
            await self.circuit_breaker.save_baselines(self.name)

            self.current_phase = self.PHASE_IDLE
            self.report_info("Cycle complete. Resting...")
        finally:
            self._is_updating = False

    async def _open_position(self, symbol: str, price: float, amount: float, sl: float):
        cost = price * amount
        self.equity -= cost
        pos = {
            'entry_price': price,
            'amount': amount,
            'stop_loss': sl,
            'take_profit': 0.0,
            'max_pnl': 0.0,
            'min_pnl': 0.0
        }
        self.active_positions[symbol] = pos
        await self.save_active_position(symbol, pos, side="LONG")
        self.report_info(f"[{symbol}] OPEN LONG {amount:.4f} @ ${price:,.2f} | SL: ${sl:,.2f}")

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
                'tp': 0.0,
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
        self.logger.info(f"Engine {self.name} shutting down. Cleaning up positions...")
        # Optional: In a real bot, we might cancel open limit orders here.
