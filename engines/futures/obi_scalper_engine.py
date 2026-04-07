import asyncio
import time
from typing import Dict, List, Any
from engines.base_engine import BaseEngine
from strategy.orderbook_imbalance import OrderBookImbalanceStrategy
from risk.circuit_breaker import circuit_breaker

class OBIScalperEngine(BaseEngine):
    """
    Aggressive High-Frequency Futures Engine.
    Uses Order Book Imbalance (OBI) to scalp micro-moves with leverage.
    Can go LONG and SHORT.
    """
    def __init__(self):
        super().__init__("Futures_OBI_Scalper")
        self.strategy = OrderBookImbalanceStrategy(depth_levels=10, imbalance_threshold=0.75)
        # Highly liquid futures pairs
        self.symbols_to_monitor = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT']
        self.active_positions = {}
        self.cached_data = {}
        
        self.leverage = 10.0 # 10x simulated leverage
        self.risk_per_trade_pct = 0.02 # Risk 2% of equity per trade
        
        # Stop Loss / Take profit as % of price
        self.tp_pct = 0.0015 # 0.15% target
        self.sl_pct = 0.0005 # 0.05% stop loss (tight!)
        
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
        
        self.logger.info(f"Engine {self.name} initialized. Leverage: {self.leverage}x")

    async def update(self):
        if not self.is_running:
            self.current_phase = self.PHASE_IDLE
            return
            
        self.current_phase = self.PHASE_SCAN
        self.report_info("Scanning Order Book Imbalances (Futures)...")
        
        for symbol in self.symbols_to_monitor:
            if not self.is_running: break
            try:
                self.current_phase = self.PHASE_DATA
                self.report_scan(symbol, "Fetching order book depth...")
                
                # Fetch order book and ticker
                ob = await self.exchange.fetch_order_book(symbol, limit=20)
                ticker = await self.exchange.fetch_ticker(symbol)
                
                if not ob or not ticker:
                    continue
                    
                price = float(ticker['last'])
                now = time.time()
                
                self.current_phase = self.PHASE_ANALYZE
                signal_data = self.strategy.evaluate_orderbook(ob)
                obi_val = signal_data.get('obi', 0.5)
                
                # Cache data for UI
                self.cached_data[symbol] = {
                    'price': price,
                    'obi': obi_val,
                    'signal': signal_data,
                    'last_update': now
                }
                
                if signal_data['signal'] != 'HOLD':
                    self.report_analyze(symbol, f"Imbalance Trigger: [bold]{signal_data['signal']}[/] ({obi_val*100:.1f}% Bids)")

                # Execution Logic
                if symbol in self.active_positions:
                    self.current_phase = self.PHASE_RISK
                    pos = self.active_positions[symbol]
                    is_long = pos['side'] == 'LONG'
                    
                    if is_long:
                        if price >= pos['take_profit']:
                            self.current_phase = self.PHASE_EXEC
                            self.report_execution(symbol, f"LONG TP HIT @ ${price:,.2f}")
                            self._close_position(symbol, price, "TAKE_PROFIT")
                        elif price <= pos['stop_loss']:
                            self.current_phase = self.PHASE_EXEC
                            self.report_execution(symbol, f"LONG SL HIT @ ${price:,.2f}")
                            self._close_position(symbol, price, "STOP_LOSS")
                    else: # SHORT
                        if price <= pos['take_profit']:
                            self.current_phase = self.PHASE_EXEC
                            self.report_execution(symbol, f"SHORT TP HIT @ ${price:,.2f}")
                            self._close_position(symbol, price, "TAKE_PROFIT")
                        elif price >= pos['stop_loss']:
                            self.current_phase = self.PHASE_EXEC
                            self.report_execution(symbol, f"SHORT SL HIT @ ${price:,.2f}")
                            self._close_position(symbol, price, "STOP_LOSS")
                            
                else: # No active position
                    self.current_phase = self.PHASE_RISK
                    if signal_data['signal'] in ['LONG', 'SHORT']:
                        if circuit_breaker.update_equity(self.get_total_equity()):
                            self.current_phase = self.PHASE_EXEC
                            side = signal_data['signal']
                            
                            # Futures Sizing: (Equity * Risk) / Price_Risk
                            # Price risk is entry * sl_pct
                            price_risk = price * self.sl_pct
                            risk_usd = self.equity * self.risk_per_trade_pct
                            size = risk_usd / price_risk
                            
                            # Check if size exceeds leveraged buying power
                            max_size = (self.equity * self.leverage) / price
                            size = min(size, max_size * 0.95) # 95% to leave room for fees
                            
                            if size > 0:
                                sl = price * (1 - self.sl_pct) if side == 'LONG' else price * (1 + self.sl_pct)
                                tp = price * (1 + self.tp_pct) if side == 'LONG' else price * (1 - self.tp_pct)
                                
                                self.report_execution(symbol, f"EXEC {side} {size:.4f}x leverage @ ${price:,.2f}")
                                self._open_position(symbol, side, price, size, sl, tp)

                self.current_phase = self.PHASE_SYNC
                await asyncio.sleep(0.3) # Extremely fast loop for HFT
                
            except Exception as e:
                self.logger.error(f"Error in OBI Engine for {symbol}: {e}")

        self.current_phase = self.PHASE_IDLE

    def _open_position(self, symbol: str, side: str, price: float, amount: float, sl: float, tp: float):
        # In futures, margin required is (price * amount) / leverage
        margin_required = (price * amount) / self.leverage
        self.equity -= margin_required # Lock margin
        
        self.active_positions[symbol] = {
            'side': side,
            'entry_price': price,
            'amount': amount,
            'margin': margin_required,
            'stop_loss': sl,
            'take_profit': tp
        }
        self.report_info(f"[{symbol}] OPEN {side} {amount:.4f} @ ${price:,.2f} | Mar: ${margin_required:,.2f}")

    def _close_position(self, symbol: str, exit_price: float, reason: str):
        pos = self.active_positions.pop(symbol)
        
        # Calculate PnL based on side
        if pos['side'] == 'LONG':
            pnl = (exit_price - pos['entry_price']) * pos['amount']
        else: # SHORT
            pnl = (pos['entry_price'] - exit_price) * pos['amount']
            
        # Return margin + pnl
        self.equity += (pos['margin'] + pnl)
        
        self.report_info(f"[{symbol}] CLOSE {pos['side']} ({reason}) @ ${exit_price:,.2f} | PnL: ${pnl:,.2f}")

    def get_total_equity(self) -> float:
        floating_pnl = 0.0
        locked_margin = 0.0
        for sym, pos in self.active_positions.items():
            locked_margin += pos['margin']
            if sym in self.cached_data:
                current_price = self.cached_data[sym]['price']
                if pos['side'] == 'LONG':
                    floating_pnl += (current_price - pos['entry_price']) * pos['amount']
                else:
                    floating_pnl += (pos['entry_price'] - current_price) * pos['amount']
        return self.equity + locked_margin + floating_pnl

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
                current_price = self.cached_data[symbol]['price']
                
            if pos['side'] == 'LONG':
                pnl_val = (current_price - pos['entry_price']) * pos['amount']
            else:
                pnl_val = (pos['entry_price'] - current_price) * pos['amount']
                
            results.append({
                'order_id': f"{symbol}_{pos['side']}",
                'symbol': symbol,
                'side': pos['side'],
                'size': pos['amount'],
                'entry': pos['entry_price'],
                'current': current_price,
                'sl': pos.get('stop_loss', 0.0),
                'tp': pos.get('take_profit', 0.0),
                'pnl': pnl_val
            })
        return results

    async def shutdown(self):
        self.stop()
        self.logger.info(f"Engine {self.name} shutting down.")
