from engines.base_engine import BaseEngine
from data.indicators import Indicators
from strategy.volatility_breakout import VolatilityBreakoutStrategy
from risk.position_sizer import PositionSizer
from risk.circuit_breaker import CircuitBreaker
from framework.context import TradingContext

class AggressiveScalperEngine(BaseEngine):
    """
    Framework-Driven High-Frequency Scalping Engine.
    Uses 1m candle closes for signals, and high-frequency ticks for exits.
    """
    def __init__(self):
        super().__init__("AggressiveScalper")
        self.strategy = VolatilityBreakoutStrategy()
        self.symbols_to_monitor = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
        self.cached_data = {}
        
        self.risk_manager = PositionSizer(max_risk_pct=0.005, atr_multiplier=1.2)
        self.circuit_breaker = CircuitBreaker(max_daily_drawdown_pct=0.02)

    async def on_start(self, ctx: TradingContext):
        await super().on_start(ctx)
        self.ctx.log("Starting Aggressive Scalper engine...", "INFO")
        for sym in self.symbols_to_monitor:
            self.ctx.subscribe_ticker(sym)
            self.ctx.subscribe_candles(sym, '1m')

    async def on_candle_closed(self, symbol: str, df):
        if symbol not in self.symbols_to_monitor: return
        
        self.ctx.report_status(symbol, "ANALYZE", "Checking breakout...")
        df = Indicators.attach_all_indicators(df)
        df['vol_sma_20'] = Indicators.calculate_sma(df, period=20, column='volume')
        price = df.iloc[-1]['close']
        
        signal = self.strategy.generate_signal(df, price)
        self.cached_data[symbol] = {
            'df': df,
            'signal': signal
        }
        
        pos = self.ctx.get_position(symbol)
        if pos:
            if signal['signal'] == 'EXIT_LONG':
                self.ctx.report_status(symbol, "EXEC", f"DYNAMIC TP / EXIT @ ${price:,.2f}")
                await self.ctx.close_position(symbol, price, "DYNAMIC_EXIT")
        else:
            if signal['signal'] == 'BUY':
                self.ctx.report_status(symbol, "RISK", "Validating scalp risk...")
                current_eq = self.ctx.get_equity()
                baseline = self.ctx.get_baseline('daily')
                
                if not self.circuit_breaker.is_tripped(current_eq, baseline):
                    atr = df.iloc[-1].get('atr_14', 0)
                    if atr > 0:
                        sl = price - (1.0 * atr)
                        tp = price + (1.5 * atr)
                        size, _ = self.risk_manager.calculate_position_size(current_eq, price, sl)
                        
                        if size > 0:
                            self.ctx.report_status(symbol, "EXEC", f"BUY SCALP {size:.4f} @ ${price:,.2f}")
                            await self.ctx.buy_limit(symbol, size, price, sl=sl, tp=tp)

    async def on_tick(self, symbol: str, ticker: dict):
        if symbol not in self.symbols_to_monitor: return
        price = float(ticker.get('last', 0))
        if price == 0: return

        pos = self.ctx.get_position(symbol)
        if pos:
            self.ctx.report_status(symbol, "RISK", "Managing Scalp Position...")
            if price >= pos.get('take_profit', float('inf')):
                self.ctx.report_status(symbol, "EXEC", f"FIXED TP HIT @ ${price:,.2f}")
                await self.ctx.close_position(symbol, price, "TAKE_PROFIT")
            elif price <= pos.get('stop_loss', 0):
                self.ctx.report_status(symbol, "EXEC", f"TIGHT SL HIT @ ${price:,.2f}")
                await self.ctx.close_position(symbol, price, "STOP_LOSS")
