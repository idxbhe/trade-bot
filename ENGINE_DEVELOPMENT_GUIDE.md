# Engine Development Guide: Crafting New Trading Brains

This guide provides a strict blueprint for creating new Trading Engines. Follow these rules to ensure your engine is compatible with the Host Dashboard and maintains high operational safety.

## 1. Boilerplate Template
Every new engine file in `/engines/` must follow this structural contract:

```python
from engines.base_engine import BaseEngine
from data.fetcher import market_collector
from data.indicators import Indicators
# Import other modules as needed (risk, strategy, etc.)

class MyNewEngine(BaseEngine):
    def __init__(self):
        super().__init__("MyUniqueEngineName")
        self.internal_state = {} # Use this to manage positions and data cache

    async def setup(self, exchange_client, config):
        """Called once when engine is loaded/started."""
        self.exchange = exchange_client
        self.config = config
        # Initialize equity, load settings, etc.
        pass

    async def update(self):
        """The main execution loop. Called periodically by the Host."""
        if not self.is_running: return
        
        # 1. Market Scanning Logic
        # 2. Data Acquisition (Use market_collector)
        # 3. Technical Analysis (Use Indicators)
        # 4. Risk Management (Use circuit_breaker / position_sizer)
        # 5. Execution Logic (Update internal_state)
        pass

    async def get_stats(self) -> dict:
        """REQUIRED: Return data for the 'Bot Status' panel."""
        return {
            'equity': 1000.0,
            'mode': 'TEST', # 'LIVE' or 'TEST'
            'active_pos_count': 0,
            'total_pnl': 0.0
        }

    async def get_active_orders(self) -> list:
        """REQUIRED: Return list of dictionaries for the Watchlist table."""
        return [{
            'symbol': 'BTC/USDT',
            'price': 50000.0,
            'rsi': 30.5,
            'adx': 15.2,
            'signal': 'BUY',
            'position': 'None',
            'pnl': '$0.00'
        }]

    async def shutdown(self):
        """Called when engine is switched or bot stopped."""
        self.stop()
        # Cleanup code here (cancel orders, etc.)
        pass
```

## 2. Core Implementation Rules (Mandatory)

### A. Non-Blocking Operations
- **NEVER** use `time.sleep()`. It will freeze the entire TUI. 
- **ALWAYS** use `await asyncio.sleep()`.

### B. Efficient Data Flow
- **Throttling:** Do not spam the exchange API. Use a minimum delay of `1.0s` between checking different symbols.
- **Caching:** Only fetch heavy data (like OHLCV/Candles) once every 60 seconds. Use ticker/last price for real-time monitoring between candle updates.

### C. Standardized Components
- Use `data.fetcher.market_collector` for all API calls (includes retries/rate-limit handling).
- Use `data.indicators.Indicators` for all TA math to ensure consistency across engines.
- Use `risk.circuit_breaker` before opening ANY new position.

### D. UI Synchronization
- `get_stats()` and `get_active_symbols()` should be **fast**. They should read from the engine's internal memory/cache, not perform new API requests.

## 3. Integration Checklist
1. Create your class in `engines/your_file.py`.
2. Open `ui/dashboard.py`.
3. Import your class at the top.
4. Add your class to `self.available_engines = [...]` in `TradingDashboard.__init__`.

## 4. Operational Safety
- **State Isolation:** An engine is a self-contained unit. It should not depend on global variables outside the `engines/` scope except for core utilities.
- **Error Resilience:** Wrap your `update()` logic in `try-except` blocks. A failure in your scanner should not crash the dashboard.
