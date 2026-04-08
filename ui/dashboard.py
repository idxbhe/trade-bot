import asyncio
import traceback
import time
import json
import os
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, DataTable
from textual.containers import Grid, Container, Vertical
from textual import work

from ui.components import ActiveOrdersTable, BotStats, SelectionModal, ActivityTicker, HistoryTable, LogModal
from core.logger import log_queue
from core.config import config
from exchange.kucoin import kucoin_client, kucoin_futures_client
from risk.circuit_breaker import CircuitBreaker

# Engines Registry
from engines.spot.hybrid_engine_v1 import HybridEngineV1
from engines.spot.scanner_engine import ScannerOnlyEngine
from engines.spot.aggressive_engine import AggressiveScalperEngine
from engines.futures.placeholder_futures_engine import PlaceholderFuturesEngine
from engines.futures.obi_scalper_engine import OBIScalperEngine

class TradingDashboard(App):
    """
    Host Dashboard with Static Engine Status.
    Focuses on a clean, centered activity ticker with a spinner.
    """
    
    CSS = """
    Grid {
        grid-size: 2 2;
        grid-columns: 1.5fr 1fr;
        grid-rows: 1fr 1fr;
        padding: 1;
        background: $surface;
    }
    
    #market-panel, #stats-container, #history-panel {
        height: 100%;
        width: 100%;
        border: solid $primary-muted;
    }
    
    #stats-container { 
        padding: 1;
        column-span: 1;
        row-span: 2;
    }
    
    BotStats {
        height: auto;
        min-height: 14;
    }
    
    ActivityTicker {
        height: 3;
        margin-top: 1;
        padding: 1 0;
        background: transparent;
        border: none;
    }
    
    RichLog { height: 100%; }
    """
    
    BINDINGS = [
        ("q", "quit_app", "Quit"),
        ("s", "toggle_bot", "Start/Stop Bot"),
        ("m", "toggle_market", "Toggle Market"),
        ("e", "cycle_engine", "Cycle Engine"),
        ("c", "manual_close", "Close Order"),
        ("l", "toggle_logs", "Logs")
    ]

    def __init__(self):
        super().__init__()
        self.bot_running = False
        self.log_history = [] # Keep log history for modal display
        self.spot_engines = [HybridEngineV1, ScannerOnlyEngine, AggressiveScalperEngine]
        self.futures_engines = [OBIScalperEngine, PlaceholderFuturesEngine]
        
        self.state_file = ".bot_state.json"
        self._load_bot_state()
        
        self.engine = self.available_engines[self.engine_idx]()
        self.engine_initialized = False
        
        # MASTER SAFETY NET: Daily drawdown limit across the whole app
        self.master_circuit_breaker = CircuitBreaker(max_daily_drawdown_pct=config.MASTER_CIRCUIT_BREAKER_PCT)

    def _load_bot_state(self):
        """Load market and engine selection from persistent storage."""
        default_market = "Spot"
        default_engine_name = "HybridEngine_V1"
        
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    default_market = state.get("current_market", "Spot")
                    default_engine_name = state.get("engine_name", "HybridEngine_V1")
            except Exception:
                pass
        
        self.current_market = default_market
        self.available_engines = self.spot_engines if self.current_market == "Spot" else self.futures_engines
        
        # Find engine index by name
        self.engine_idx = 0
        for i, eng_cls in enumerate(self.available_engines):
            if eng_cls().name == default_engine_name:
                self.engine_idx = i
                break

    def save_bot_state(self):
        """Save current market and engine selection to persistent storage."""
        try:
            state = {
                "current_market": self.current_market,
                "engine_name": self.engine.name
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            self.log_history.append(f"[bold red]Failed to save state:[/] {e}")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Grid():
            with Container(id="market-panel"):
                self.active_orders_table = ActiveOrdersTable()
                yield self.active_orders_table
            
            with Vertical(id="stats-container"):
                # Bot Stats
                self.bot_stats = BotStats()
                yield self.bot_stats

                # NEW: Static Activity Ticker
                self.activity_ticker = ActivityTicker()
                yield self.activity_ticker
                
            with Container(id="history-panel"):
                self.history_table = HistoryTable()
                yield self.history_table
        yield Footer()

    async def on_active_orders_table_manual_close_request(self, message: ActiveOrdersTable.ManualCloseRequest) -> None:
        """Handle manual close request message from the orders table."""
        order_id = message.order_id
        self.activity_ticker.message = f"Requesting manual close for {order_id}..."
        await self.engine.close_position(order_id)
        # Re-fetch data immediately
        await self.update_ui_sync()

    async def action_manual_close(self) -> None:
        """Handle manual position close when 'c' key is pressed (fallback for App binding)."""
        row_idx = self.active_orders_table.cursor_row
        if row_idx is not None:
            try:
                row_key = self.active_orders_table.get_row_key_at(row_idx)
                order_id = str(row_key.value)
                if order_id:
                    self.activity_ticker.message = f"Requesting manual close for {order_id}..."
                    await self.engine.close_position(order_id)
                    await self.update_ui_sync()
            except Exception as e:
                self.activity_ticker.message = f"[bold red]Close Error:[/] {e}"
        else:
            self.activity_ticker.message = "[bold yellow]Select a position to close first.[/]"

    async def update_ui_sync(self):
        """Fetch data from the current active engine with robust error handling."""
        # Safety: Ensure dashboard is fully mounted and widgets are ready
        if not self.is_mounted:
            return

        # Defensive: Check if all expected widgets exist and are not None
        widgets = ["bot_stats", "activity_ticker", "active_orders_table", "history_table"]
        for w in widgets:
            if not getattr(self, w, None):
                return

        # Defensive: Check engine
        if not getattr(self, "engine", None):
            return

        try:
            stats = await self.engine.get_stats()
            if stats is None: stats = {}
            
            orders = await self.engine.get_active_orders()
            if orders is None: orders = []
            
            history = await self.engine.get_order_history()
            if history is None: history = []
            
            equity = stats.get('equity', 0)
            self.sub_title = f"BOT MONITOR | Engine: {self.engine.name} | Balance: ${equity:,.2f}"
            self.bot_stats.stats_data = stats
            
            # Update the static activity ticker
            activity = getattr(self.engine, "latest_activity", "Engine processing...")
            self.activity_ticker.message = str(activity) if activity is not None else "Engine processing..."
            
            # Clear verbose queue to keep it non-persistent
            v_queue = getattr(self.engine, "verbose_queue", None)
            if v_queue is not None:
                v_queue.clear()
            
            # Update Active Orders
            new_order_ids = set()
            for item in orders:
                if not item or 'order_id' not in item: continue
                order_id = item['order_id']
                new_order_ids.add(order_id)
                self.active_orders_table.update_order_data(
                    order_id=order_id, 
                    symbol=item.get('symbol', '???'), 
                    side=item.get('side', '???'), 
                    size=item.get('size', 0), 
                    entry=item.get('entry', 0), 
                    current=item.get('current', 0), 
                    sl=item.get('sl', 0), 
                    tp=item.get('tp', 0), 
                    pnl=item.get('pnl', 0)
                )

            # Remove positions that are no longer active
            try:
                # Textual DataTable rows keys can be accessed safely via .rows
                current_row_keys = list(self.active_orders_table.rows.keys())
                for row_key in current_row_keys:
                    row_id = str(row_key.value)
                    if row_id not in new_order_ids:
                        self.active_orders_table.remove_row(row_key)
            except Exception:
                pass

            # Update History Table
            # Show only last 20 entries
            recent_history = history[-20:] if history else []
            if self.history_table.row_count != len(recent_history):
                self.history_table.clear()
                for item in recent_history:
                    self.history_table.add_history_entry(
                        item.get('time', '??:??:??'), 
                        item.get('symbol', '???'), 
                        item.get('side', '???'), 
                        item.get('amount', 0), 
                        item.get('exit', 0), 
                        item.get('pnl', 0), 
                        item.get('max_pnl', 0.0), 
                        item.get('min_pnl', 0.0),
                        item.get('reason', 'UNKNOWN')
                    )
        except Exception as e:
            if hasattr(self, "activity_ticker") and self.activity_ticker:
                self.activity_ticker.message = f"[bold red]UI Error:[/] {str(e)}"

    def on_mount(self) -> None:
        self.title = "🤖 KUCOIN TRADE BOT"
        self.set_interval(0.5, self.flush_logs)
        self.set_interval(2.0, self.update_ui_sync)
        self.activity_ticker.message = "Engine activity will appear here..."
        self.active_orders_table.focus()

    def flush_logs(self) -> None:
        """Route system logs (errors/warnings) to history buffer and active modal."""
        while not log_queue.empty():
            msg = log_queue.get_nowait()
            self.log_history.append(msg)
            # Limit history size to 1000 lines for memory efficiency
            if len(self.log_history) > 1000:
                self.log_history.pop(0)
            
            # If the LogModal is currently open, write to it live
            if isinstance(self.screen, LogModal):
                self.screen.log_widget.write(msg)

    def action_toggle_logs(self) -> None:
        """Open the log history modal."""
        self.push_screen(LogModal(self.log_history))

    async def action_toggle_market(self) -> None:
        if self.bot_running:
            self.activity_ticker.message = "[bold red]Stop the bot first to change market.[/bold red]"
            return
            
        def handle_market_selection(selected_market: str):
            if selected_market and selected_market != self.current_market:
                self.activity_ticker.message = f"Switching to {selected_market}..."
                self.current_market = selected_market
                self.available_engines = self.spot_engines if self.current_market == "Spot" else self.futures_engines
                asyncio.create_task(self.perform_engine_swap(0))
                # Note: save_bot_state is called inside perform_engine_swap

        self.push_screen(
            SelectionModal("Pilih Market", [("Spot Market", "Spot"), ("Futures Market", "Futures")]),
            handle_market_selection
        )

    async def action_cycle_engine(self) -> None:
        if self.bot_running:
            self.activity_ticker.message = "[bold red]Stop the bot first to change engine.[/bold red]"
            return
            
        engine_options = [(eng().name, i) for i, eng in enumerate(self.available_engines)]
        
        def handle_engine_selection(selected_idx: int):
            if selected_idx is not None:
                new_engine_name = self.available_engines[selected_idx]().name
                self.activity_ticker.message = f"Switching to engine {new_engine_name}..."
                asyncio.create_task(self.perform_engine_swap(selected_idx))
                # Note: save_bot_state is called inside perform_engine_swap

        self.push_screen(
            SelectionModal(f"Pilih Engine {self.current_market}", engine_options),
            handle_engine_selection
        )

    async def perform_engine_swap(self, new_idx: int):
        self.bot_running = False
        self.activity_ticker.message = f"Shutting down {self.engine.name}..."
        if self.engine_initialized:
            await self.engine.shutdown()
        
        self.engine_idx = new_idx
        self.engine = self.available_engines[self.engine_idx]()
        self.engine_initialized = False
        self.activity_ticker.message = f"Swapped to {self.engine.name}."
        self.save_bot_state()

    async def action_toggle_bot(self) -> None:
        if not self.bot_running:
            self.bot_running = True
            if not self.engine_initialized:
                client = kucoin_client if self.current_market == "Spot" else kucoin_futures_client
                await self.engine.setup(client, config)
                self.engine_initialized = True
            self.engine.start()
            self.activity_ticker.message = "Engine Heartbeat Started."
            self.run_bot_worker()
        else:
            self.bot_running = False
            self.engine.stop()
            self.activity_ticker.message = "Engine Heartbeat Stopped."

    @work(exclusive=True)
    async def run_bot_worker(self) -> None:
        while self.bot_running:
            try:
                # 0. Safety: Check engine
                if not getattr(self, "engine", None):
                    await asyncio.sleep(1)
                    continue

                # 1. Check Master Safety Net
                total_equity = self.engine.get_total_equity()
                if not self.master_circuit_breaker.update_equity(total_equity):
                    if self.is_mounted and hasattr(self, "activity_ticker"):
                        self.activity_ticker.message = "[bold red]MASTER CIRCUIT BREAKER TRIPPED! Stopping Bot...[/]"
                    self.bot_running = False
                    self.engine.stop()
                    break

                # 2. Update Engine
                await self.engine.update()
                
                # 3. Update Activity Ticker from Stats
                if self.is_mounted and hasattr(self, "activity_ticker"):
                    stats = await self.engine.get_stats()
                    msg = stats.get('status_message', '') if stats else ''
                    if msg:
                        self.activity_ticker.message = str(msg)
                
                await asyncio.sleep(0.5) 
            except Exception as e:
                # Standardize logging to history since log_widget is modal-only
                self.log_history.append(f"[bold red]Engine Error:[/] {str(e)}")
                await asyncio.sleep(5)

    async def action_quit_app(self) -> None:
        self.bot_running = False
        if self.engine_initialized:
            await self.engine.shutdown()
        # Ensure ccxt clients are closed properly
        await kucoin_client.close()
        await kucoin_futures_client.close()
        self.exit()

if __name__ == "__main__":
    TradingDashboard().run()
