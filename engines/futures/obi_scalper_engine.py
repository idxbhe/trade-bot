import time
from engines.base_engine import BaseEngine
from strategy.orderbook_imbalance import OrderBookImbalanceStrategy
from risk.circuit_breaker import CircuitBreaker
from framework.context import TradingContext

class OBIScalperEngine(BaseEngine):
    """
    Framework-Driven Futures Engine: OrderBook Imbalance Scalper.
    """
    def __init__(self):
        super().__init__("OBI_Futures_Scalper")
        self.strategy = OrderBookImbalanceStrategy(imbalance_threshold=0.25)
        self.symbols_to_monitor = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT']
        self.cached_data = {}
        
        self.leverage = 10
        self.risk_per_trade_pct = 0.02
        self.sl_pct = 0.005
        self.tp_pct = 0.015
        
        self.circuit_breaker = CircuitBreaker(max_daily_drawdown_pct=0.03)

    async def on_start(self, ctx: TradingContext):
        await super().on_start(ctx)
        self.ctx.log(f"Starting OBI Scalper ({self.leverage}x lev)...", "INFO")
        for sym in self.symbols_to_monitor:
            self.ctx.subscribe_ticker(sym)

    async def on_tick(self, symbol: str, ticker: dict):
        if symbol not in self.symbols_to_monitor: return
        price = float(ticker.get('last', 0))
        if price == 0: return
        
        pos = self.ctx.get_position(symbol)
        
        if pos:
            self.ctx.report_status(symbol, "RISK", "Monitoring Futures Pos...")
            if pos['side'] == 'LONG':
                if price >= pos.get('take_profit', float('inf')):
                    self.ctx.report_status(symbol, "EXEC", f"LONG TP HIT @ ${price:,.2f}")
                    await self.ctx.close_position(symbol, price, "TAKE_PROFIT")
                elif price <= pos.get('stop_loss', 0):
                    self.ctx.report_status(symbol, "EXEC", f"LONG SL HIT @ ${price:,.2f}")
                    await self.ctx.close_position(symbol, price, "STOP_LOSS")
            else:
                if price <= pos.get('take_profit', 0):
                    self.ctx.report_status(symbol, "EXEC", f"SHORT TP HIT @ ${price:,.2f}")
                    await self.ctx.close_position(symbol, price, "TAKE_PROFIT")
                elif price >= pos.get('stop_loss', float('inf')):
                    self.ctx.report_status(symbol, "EXEC", f"SHORT SL HIT @ ${price:,.2f}")
                    await self.ctx.close_position(symbol, price, "STOP_LOSS")
            return
            
        # Throttled REST order book fetch to prevent API ban (WS fallback)
        now = time.time()
        if symbol not in self.cached_data or (now - self.cached_data[symbol].get('last_update', 0)) > 1.5:
            self.ctx.report_status(symbol, "DATA", "Fetching OrderBook...")
            try:
                ob = await self.ctx.get_order_book(symbol, limit=20)
                if ob:
                    self.ctx.report_status(symbol, "ANALYZE", "Calculating OBI...")
                    signal = self.strategy.evaluate_orderbook(ob)
                    self.cached_data[symbol] = {'signal': signal, 'last_update': now}
                    
                    sig = signal['signal']
                    if sig in ['BUY', 'SELL']:
                        self.ctx.report_status(symbol, "RISK", "Checking Margin Risk...")
                        current_eq = self.ctx.get_equity()
                        baseline = self.ctx.get_baseline('daily')
                        
                        if not self.circuit_breaker.is_tripped(current_eq, baseline):
                            side = 'LONG' if sig == 'BUY' else 'SHORT'
                            trade_value = current_eq * self.risk_per_trade_pct * self.leverage
                            size = trade_value / price
                            
                            if size > 0:
                                sl = price * (1 - self.sl_pct) if side == 'LONG' else price * (1 + self.sl_pct)
                                tp = price * (1 + self.tp_pct) if side == 'LONG' else price * (1 - self.tp_pct)
                                
                                self.ctx.report_status(symbol, "EXEC", f"EXEC {side} {size:.4f}x @ ${price:,.2f}")
                                if side == 'LONG':
                                    await self.ctx.buy_limit(symbol, size, price, sl=sl, tp=tp)
                                else:
                                    await self.ctx.sell_limit(symbol, size, price, sl=sl, tp=tp)
            except Exception as e:
                pass
