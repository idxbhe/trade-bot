import asyncio
import traceback
import time
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, DataTable
from textual.containers import Grid, Container, Vertical
from textual import work

from ui.components import ActiveOrdersTable, BotStats, SelectionModal, ActivityTicker, HistoryTable, LogModal
from core.logger import log_queue
from core.config import config
from exchange.kucoin import kucoin_client, kucoin_futures_client

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
        
        self.current_market = "Spot"
        self.available_engines = self.spot_engines
        self.engine_idx = 0
        self.engine = self.available_engines[self.engine_idx]()
        self.engine_initialized = False

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

    async def action_manual_close(self) -> None:
        """Handle manual position close when 'c' key is pressed while ActiveOrdersTable is focused."""
        if self.focused == self.active_orders_table:
            row_idx = self.active_orders_table.cursor_row
            if row_idx is not None:
                try:
                    row_key = self.active_orders_table.get_row_key_at(row_idx)
                    order_id = str(row_key.value)
                    if order_id:
                        self.activity_ticker.message = f"Requesting manual close for {order_id}..."
                        await self.engine.close_position(order_id)
                        # Re-fetch data immediately
                        await self.update_ui_sync()
                except Exception:
                    pass

    async def update_ui_sync(self):
        """Fetch data from the current active engine with robust error handling."""
        try:
            stats = await self.engine.get_stats()
            orders = await self.engine.get_active_orders()
            history = await self.engine.get_order_history()
            
            self.sub_title = f"BOT MONITOR | Engine: {self.engine.name} | Balance: ${stats.get('equity', 0):,.2f}"
            self.bot_stats.stats_data = stats
            
            # Update the static activity ticker
            self.activity_ticker.message = self.engine.latest_activity
            
            # Clear verbose queue to keep it non-persistent as requested
            self.engine.verbose_queue.clear()
            
            # Update Active Orders
            new_order_ids = set()
            if orders:
                for item in orders:
                    order_id = item['order_id']
                    new_order_ids.add(order_id)
                    self.active_orders_table.update_order_data(
                        order_id=order_id, 
                        symbol=item['symbol'], 
                        side=item['side'], 
                        size=item['size'], 
                        entry=item['entry'], 
                        current=item['current'], 
                        sl=item['sl'], 
                        tp=item['tp'], 
                        pnl=item['pnl']
                    )

            # Remove positions that are no longer active
            current_rows = [str(k.value) for k in self.active_orders_table.rows.keys()]
            for row_id in current_rows:
                if row_id not in new_order_ids:
                    self.active_orders_table.remove_row(row_id)

            # Update History Table
            if history:
                # Show only last 20 entries
                recent_history = history[-20:]
                # For history, it's safer to clear and re-add or just add new ones
                # Since history is read-only and growing, we'll just check count
                if self.history_table.row_count != len(recent_history):
                    self.history_table.clear()
                    for item in recent_history:
                        self.history_table.add_history_entry(
                            item['time'], item['symbol'], item['side'], 
                            item['amount'], item['exit'], item['pnl'], 
                            item.get('max_pnl', 0.0), item.get('min_pnl', 0.0),
                            item['reason']
                        )
        except Exception as e:
            self.activity_ticker.message = f"[bold red]UI Error:[/] {e}"

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
                # Tell engine to log verbosely to its internal status_message 
                # or we can pass the engine_log widget handle.
                # Standard choice: Let the engine update its status_message, 
                # and we display it in the log.
                await self.engine.update()
                
                # Write current engine status to verbose log
                stats = await self.engine.get_stats()
                msg = stats.get('status_message', '')
                if msg:
                    self.activity_ticker.message = msg
                
                await asyncio.sleep(0.5) 
            except Exception as e:
                self.log_widget.write(f"[bold red]Engine Error: {e}[/bold red]")
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
