# Trade-Bot Engine Architecture (Plug-and-Play)

## System Overview
This bot operates on a **Host-Engine** architecture. The `TradingDashboard` (found in `ui/dashboard.py`) acts as a passive host that handles the Text User Interface (TUI) and user input. All core trading logic, including market scanning, technical analysis, and order execution, is delegated to a "Plug-and-Play" **Engine**.

## The Engine Contract (`BaseEngine`)
Every engine must inherit from `BaseEngine` (located in `engines/base_engine.py`). This ensures the Host can interact with any engine using a standardized interface.

### Mandatory Methods:
- `async setup(exchange_client, config)`: Initializes the engine with API clients and environment settings.
- `async update()`: The "heartbeat" of the bot. Performed every cycle to scan the market and execute trades. Must check `self.is_running` to allow clean termination.
- `async get_stats()`: Returns a dictionary containing performance metrics (Equity, PnL, Mode) for the `BotStats` widget.
- `async get_active_orders()`: Returns a list of dictionaries containing symbol data for the ActiveOrdersTable.
- `async get_order_history()`: Returns a list of recently closed positions for the HistoryTable.
- `async close_position(order_id)`: Handles manual requests to close a specific position.
- `async shutdown()`: Handles clean exit procedures (e.g., cancelling open orders) before the engine is replaced or the app quits.

## How to Add a New Engine (Plug-and-Play)
1.  **Create:** Add a new file in the `engines/spot/` or `engines/futures/` directory.
2.  **Implement:** Subclass `BaseEngine` and fill in the mandatory methods.
3.  **Register:** In `ui/dashboard.py`, import your new class and add it to either `self.spot_engines` or `self.futures_engines` in the `__init__` method.

## Operational Security
- **Switching Lock:** Engine selection and Market selection are locked via the UI while the bot is `Running`. You must `Stop` the bot to switch.
- **Manual Control:** Users can manually close any position by selecting it in the UI and pressing `c`.

## Current Engines
1.  **HybridEngineV1:** Active spot trader using Mean Reversion.
2.  **AggressiveScalperEngine:** High-frequency spot breakout strategy.
3.  **OBIScalperEngine:** Futures strategy using Order Book Imbalance.
4.  **ScannerOnlyEngine:** Passive monitor for spot prices.
