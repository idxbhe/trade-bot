# Engine Development Guide: Crafting New Trading Brains

This guide provides the blueprint for creating new Trading Engines compatible with the Kernel-Context architecture.

## 1. Boilerplate Template
A modern engine inherits from `BaseEngine` and interacts with the system via the `TradingContext` API.

```python
from engines.base_engine import BaseEngine
from framework.context import TradingContext

class MyNewEngine(BaseEngine):
    def __init__(self):
        super().__init__("MyUniqueEngineName")

    async def on_start(self, ctx: TradingContext):
        """Called once when the engine starts. Subscribe to data here."""
        self.ctx = ctx
        await ctx.subscribe_ticker("BTC/USDT")
        await ctx.subscribe_candles("ETH/USDT", "15m")
        ctx.log("Engine initialized and subscribed.")

    async def on_tick(self, symbol: str, ticker: dict):
        """Asynchronously called whenever a new price update arrives."""
        price = ticker['last']
        # Implement strategy logic...

    async def on_candle_closed(self, symbol: str, df: pd.DataFrame):
        """Asynchronously called when a candle closes. 'df' contains 200 bars."""
        # 'df' is a deep copy; you can modify it safely.
        # Indicators are already stabilized due to the 200-bar history.
        pass

    async def on_stop(self):
        """Clean up resources or close positions if needed."""
        pass
```

## 2. Core Implementation Rules

### A. Non-Blocking Design
The Kernel uses an asynchronous event loop. 
- **NEVER** use `time.sleep()`. 
- **ALWAYS** use `await asyncio.sleep()`.
- Logic in `on_tick` and `on_candle_closed` should be efficient to avoid lagging the event queue.

### B. Leveraging the TradingContext
Engines must use the `ctx` object for all external interactions:
- **Data**: `await ctx.get_historical_data(symbol, limit=200)`
- **Execution**: `await ctx.buy_limit(symbol, amount, price, sl, tp)`
- **State**: `ctx.get_position(symbol)`, `ctx.get_equity()`

### C. Indicator Accuracy
Calculations must align with exchange standards:
- **Standard Deviation**: Use `ddof=0` (Population Std Dev) for Bollinger Bands.
- **Warm-up**: Always request at least **200 bars** of historical data to ensure EWM-based indicators (RSI, ADX, ATR) are fully stabilized.

### D. Concurrency Safety
The Kernel ensures that `on_tick` and `on_candle_closed` for the **same symbol** are processed sequentially via symbol-level locks. You do not need to implement internal locking for state shared between these events for the same symbol.

## 3. Risk Management
- **Automatic Sizing**: The `Kernel` and `OrderManager` handle complex sizing logic, but you must provide accurate `sl` (Stop Loss) and `tp` (Take Profit) values.
- **Futures Leverage**: If your engine is designated for Futures, the `PositionSizer` will automatically account for leverage in its buying power calculations.

## 4. Operational Safety
- **Post-Only Verification**: By default, `buy_limit` and `sell_limit` use `post_only=True`. The Kernel will automatically adjust your order price to ensure it is a Maker order or log a warning if the spread is too tight.
- **Error Resilience**: The Kernel wraps engine callbacks in try-except blocks. An error in your engine will be logged but will not crash the entire Framework.
