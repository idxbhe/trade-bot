from engines.base_engine import BaseEngine
from data.indicators import Indicators
from strategy.mean_reversion import MeanReversionStrategy
from risk.position_sizer import PositionSizer
from risk.circuit_breaker import CircuitBreaker
from framework.context import TradingContext

class HybridEngineV1(BaseEngine):
    """
    Framework-Driven Engine: Hybrid Mean Reversion + Scanning.
    Pure strategy logic, zero infrastructure code. Uses WebSockets for ticks and candles.
    """
    def __init__(self):
        super().__init__("HybridEngine_V1")
        self.strategy = MeanReversionStrategy()
        self.symbols_to_monitor = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT']
        self.cached_data = {}
        
        # Hybrid Mean Reversion: 1.5% risk per trade, 2.5x ATR stop loss
        self.risk_manager = PositionSizer(max_risk_pct=0.015, atr_multiplier=2.5)
        # Standard Circuit Breaker: 5% daily drawdown
        self.circuit_breaker = CircuitBreaker(max_daily_drawdown_pct=0.05)

    async def on_start(self, ctx: TradingContext):
        await super().on_start(ctx)
        self.ctx.log("Starting engine... subscribing to data feeds.", "INFO")
        for sym in self.symbols_to_monitor:
            self.ctx.subscribe_ticker(sym)
            self.ctx.subscribe_candles(sym, '15m')

    async def on_candle_closed(self, symbol: str, df):
        """Triggered precisely when a 15m candle closes via WebSocket."""
        if symbol not in self.symbols_to_monitor: return
        
        self.ctx.report_status(symbol, "ANALYZE", "Calculating Indicators...")
        df = Indicators.attach_all_indicators(df)
        price = df.iloc[-1]['close']
        signal = self.strategy.generate_signal(df, price)
        
        self.cached_data[symbol] = {
            'df': df,
            'signal': signal
        }
        
        # Process Entry or Dynamic Exit logic only on candle close
        pos = self.ctx.get_position(symbol)
        if pos:
            if signal['signal'] == 'EXIT_LONG':
                self.ctx.report_status(symbol, "EXEC", f"TAKE PROFIT / EXIT SIGNAL @ ${price:,.2f}")
                await self.ctx.close_position(symbol, price, "SIGNAL_EXIT")
        else:
            if signal['signal'] == 'BUY':
                self.ctx.report_status(symbol, "RISK", "Validating account risk...")
                current_eq = self.ctx.get_equity()
                baseline = self.ctx.get_baseline('daily')
                
                if not self.circuit_breaker.is_tripped(current_eq, baseline):
                    atr = df.iloc[-1].get('atr_14', 0)
                    sl = self.risk_manager.calculate_stop_loss(price, atr)
                    size, _ = self.risk_manager.calculate_position_size(current_eq, price, sl)
                    
                    if size > 0:
                        self.ctx.report_status(symbol, "EXEC", f"BUY SIGNAL {size:.4f} @ ${price:,.2f}")
                        await self.ctx.buy_limit(symbol, size, price, sl=sl, tp=0.0)
                        
        self.ctx.report_status(symbol, "SYNC", "Candle Processed OK")

    async def on_tick(self, symbol: str, ticker: dict):
        """Triggered continuously for high-frequency stop-loss checking."""
        if symbol not in self.symbols_to_monitor: return
        price = float(ticker.get('last', 0))
        if price == 0: return

        pos = self.ctx.get_position(symbol)
        if pos:
            # Immediate Stop Loss Check
            if price <= pos['stop_loss']:
                self.ctx.report_status(symbol, "EXEC", f"STOP LOSS HIT @ ${price:,.2f}")
                await self.ctx.close_position(symbol, price, "STOP_LOSS")
            else:
                self.ctx.report_status(symbol, "RISK", "Monitoring active position...")
