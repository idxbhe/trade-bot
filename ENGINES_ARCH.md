# Trade-Bot Architecture (Kernel-Engine)

## System Overview
The bot operates on a **Kernel-Engine** architecture designed for high-performance WebSocket-driven trading. The **Kernel** (found in `framework/kernel.py`) acts as the central coordinator, managing data streams, engine lifecycles, and concurrent execution safety.

## Core Components
### 1. The Kernel (`framework/kernel.py`)
The "Brain" of the system. It handles:
- **Concurrency Control**: Maintains per-symbol `asyncio` locks to prevent race conditions during simultaneous ticker and candle events.
- **Engine Management**: Loads, starts, and unloads engines dynamically.
- **Order Orchestration**: Interfaces with the `OrderManager` to execute trades with proactive price adjustments (e.g., Post-Only spread protection).

### 2. TradingContext (`framework/context.py`)
A Dependency Injection (DI) API provided to every engine. Engines interact *only* with the context, which keeps them decoupled from the Kernel's internal complexity.
- **Subscriptions**: Engines ask the context to subscribe to symbols.
- **Execution**: Engines call `buy_limit`, `sell_limit`, or `close_position` via the context.
- **State**: Provides access to equity, positions, and balances.

### 3. DataStream (`framework/data_stream.py`)
A robust WebSocket manager using CCXT Pro.
- **Smart Caching**: Implements in-memory OHLCV caching to eliminate redundant REST API calls and prevent Rate Limit Bans.
- **Garbage Collection**: Automatically terminates WebSocket workers when no active engine requires them.

### 4. Risk Management Layer
- **PositionSizer**: Calculates sizes based on account equity, risk %, and ATR volatility. Now fully supports **Futures Leverage** for accurate buying power calculation.
- **Slippage Protection**: Implements a multi-layered price reconciliation (Matching Engine polling + WebSocket fallback) for accurate PnL recording.

## Operational Safety
- **Race Condition Prevention**: Per-symbol locking ensures that a new quote doesn't trigger a trade while a previous one is still being processed.
- **Futures Stability**: Orders are strictly scoped. `reduceOnly` is used only on Futures to prevent accidental "Position Flipping".
- **Maker-Only Execution**: The Kernel proactively adjusts limit prices based on real-time Order Book depth to ensure "Post-Only" orders are not rejected by the exchange.

## Current Engines
1.  **HybridEngineV1**: Spot Mean Reversion.
2.  **OBIScalperEngine**: Futures Order Book Imbalance scalper.
3.  **NeutralGridEngine**: Spot market grid maker.
