# Engine Development Guide: Crafting New Trading Brains

This guide provides a strict blueprint for creating new Trading Engines. Follow these rules to ensure your engine is compatible with the Host Dashboard and maintains high operational safety.

## 1. Boilerplate Template
Every new engine file in `/engines/` must follow this structural contract:

```python
from engines.base_engine import BaseEngine
from data.fetcher import market_collector
from data.indicators import Indicators
import time

class MyNewEngine(BaseEngine):
    def __init__(self):
        super().__init__("MyUniqueEngineName")
        self.active_positions = {}
        self.cached_data = {}

    async def setup(self, exchange_client, config):
        """Called once when engine is loaded/started."""
        self.exchange = exchange_client
        self.config = config
        pass

    async def update(self):
        """The main execution loop. Called periodically by the Host."""
        if not self.is_running: return
        
        for symbol in self.symbols_to_monitor:
            # MANDATORY: Check is_running inside loop to allow instant stop
            if not self.is_running: break 
            
            try:
                self.report_scan(symbol, "Scanning...")
                # ... fetch data and analyze ...
                self.report_analyze(symbol, "Analysis complete.")
                # ... execution logic ...
            except Exception as e:
                self.logger.error(f"Error: {e}")

    async def _close_position(self, symbol, price, reason):
        """Helper to close position and record history."""
        pos = self.active_positions.pop(symbol)
        # ... calculate pnl ...
        
        # RECORD HISTORY (REQUIRED for UI)
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

    async def get_stats(self) -> dict:
        """REQUIRED: Return data for the 'Bot Status' panel."""
        return {
            'equity': self.get_total_equity(),
            'mode': 'TEST', 
            'active_pos_count': len(self.active_positions),
            'total_pnl': 0.0,
            # ... other metrics ...
        }

    def get_total_equity(self) -> float:
        """REQUIRED: Return current total equity (cash + open positions)."""
        return self.equity

    async def get_active_orders(self) -> list:
        """REQUIRED: Return list of dictionaries for ActiveOrdersTable."""
        return [{
            'order_id': 'BTC/USDT_LONG',
            'symbol': 'BTC/USDT',
            'side': 'LONG',
            'size': 0.1, # Asset amount
            'entry': 50000.0,
            'current': 51000.0,
            'sl': 49000.0,
            'tp': 55000.0,
            'pnl': 100.0
        }]

    async def close_position(self, order_id: str):
        """REQUIRED: Handle manual close request from UI (key 'c')."""
        symbol = order_id.rsplit('_', 1)[0]
        if symbol in self.active_positions:
            await self._close_position(symbol, current_price, "MANUAL_CLOSE")

    async def shutdown(self):
        """Called when engine is switched or bot stopped."""
        self.stop()
        pass
```

## 2. Core Implementation Rules (Mandatory)

### A. Non-Blocking Operations
- **NEVER** use `time.sleep()`. It will freeze the entire TUI. 
- **ALWAYS** use `await asyncio.sleep()`.

### B. Efficient Data Flow
- **Throttling:** Do not spam the exchange API. Use a minimum delay of `1.0s` between checking different symbols.
- **Caching:** Only fetch heavy data (like OHLCV/Candles) once every 60 seconds. Use ticker/last price for real-time monitoring between candle updates.

### C. Standardized Risk Management
- **Local Risk:** Always initialize `self.risk_manager = PositionSizer(...)` and `self.circuit_breaker = CircuitBreaker(...)` in the `setup()` method.
- **Circuit Breaker Check:** Always call `self.circuit_breaker.update_equity(self.get_total_equity())` before opening ANY new position.
- **Sizing:** Use `self.risk_manager.calculate_position_size(...)` to calculate orders.

### D. UI Synchronization
- `get_stats()`, `get_active_orders()`, and `get_order_history()` should be **fast**. They should read from the engine's internal memory/cache, not perform new API requests.

## 3. Integration Checklist
1. Create your class in `engines/spot/` or `engines/futures/`.
2. Open `ui/dashboard.py`.
3. Import your class at the top.
4. Add your class to either `self.spot_engines` or `self.futures_engines` in `TradingDashboard.__init__`.

## 4. Operational Safety
- **Non-Blocking Loops:** You MUST check `if not self.is_running: break` inside any loop that iterates over symbols in `update()`.
- **State Isolation:** An engine is a self-contained unit. It should not depend on global variables outside the `engines/` scope except for core utilities.
- **Error Resilience:** Wrap your `update()` logic in `try-except` blocks. A failure in your scanner should not crash the dashboard.
