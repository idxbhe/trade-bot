# Trade-Bot Engine Architecture (Plug-and-Play)

## System Overview
This bot operates on a **Host-Engine** architecture. The `TradingDashboard` (found in `ui/dashboard.py`) acts as a passive host that handles the Text User Interface (TUI) and user input. All core trading logic, including market scanning, technical analysis, and order execution, is delegated to a "Plug-and-Play" **Engine**.

## The Engine Contract (`BaseEngine`)
Every engine must inherit from `BaseEngine` (located in `engines/base_engine.py`). This ensures the Host can interact with any engine using a standardized interface.

### Mandatory Methods:
- `async setup(exchange_client, config)`: Initializes the engine with API clients and environment settings.
- `async update()`: The "heartbeat" of the bot. Performed every cycle to scan the market and execute trades.
- `async get_stats()`: Returns a dictionary containing performance metrics (Equity, PnL, Mode) for the `BotStats` widget.
- `async get_active_symbols()`: Returns a list of dictionaries containing symbol data (Price, RSI, ADX, Signal, Position, PnL) for the `WatchlistTable`.
- `async shutdown()`: Handles clean exit procedures (e.g., cancelling open orders) before the engine is replaced or the app quits.

## How to Add a New Engine (Plug-and-Play)
1.  **Create:** Add a new file in the `engines/` directory (e.g., `my_new_strategy.py`).
2.  **Implement:** Subclass `BaseEngine` and fill in the mandatory methods.
3.  **Register:** In `ui/dashboard.py`, import your new class and add it to the `self.available_engines` list inside the `__init__` method.

## Operational Security
- **Switching Lock:** Engine selection is locked via the UI while the bot is `Running`. You must `Stop` the bot to switch engines.
- **State Isolation:** Each engine maintains its own internal state, preventing cross-contamination of trading logic.
- **Error Handling:** The Host wraps engine calls in try-except blocks to prevent a crash in one engine from killing the entire application.

## Current Engines
1.  **HybridEngineV1:** Active trader using Mean Reversion and multi-symbol scanning.
2.  **ScannerOnlyEngine:** Passive monitor that only updates prices without trading.
