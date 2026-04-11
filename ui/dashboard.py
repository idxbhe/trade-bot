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

# Framework
from framework.kernel import kernel

# Engines Registry
from engines.spot.hybrid_engine_v1 import HybridEngineV1
# These still need to be migrated, but we load them as classes
from engines.spot.scanner_engine import ScannerOnlyEngine
from engines.spot.aggressive_engine import AggressiveScalperEngine
from engines.futures.placeholder_futures_engine import PlaceholderFuturesEngine
from engines.futures.obi_scalper_engine import OBIScalperEngine

class TradingDashboard(App):
    """
    Host Dashboard mapped to the new Framework Kernel.
    Instead of actively calling engine.update(), it passively reads kernel state.
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
        ("t", "toggle_mode", "Toggle Mode"),
        ("m", "toggle_market", "Toggle Market"),
        ("e", "cycle_engine", "Cycle Engine"),
        ("l", "toggle_logs", "Logs")
    ]

    def __init__(self):
        super().__init__()
        self.bot_running = False
        self.is_processing = False # Lock flag for async operations
        self.log_history = [] 
        self.spot_engines = [HybridEngineV1, ScannerOnlyEngine, AggressiveScalperEngine]
        self.futures_engines = [OBIScalperEngine, PlaceholderFuturesEngine]
        
        self.state_file = ".bot_state.json"
        self._load_bot_state()
        
        self.engine_name = self.available_engines[self.engine_idx]().name
        self.engine_initialized = False
        self.execution_mode = 'LIVE' if config.KUCOIN_ENV.lower() != 'sandbox' else 'TEST'

    def _load_bot_state(self):
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
        
        self.engine_idx = 0
        for i, eng_cls in enumerate(self.available_engines):
            if eng_cls().name == default_engine_name:
                self.engine_idx = i
                break

    def save_bot_state(self):
        try:
            state = {
                "current_market": self.current_market,
                "engine_name": self.engine_name
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
                self.bot_stats = BotStats()
                yield self.bot_stats
                self.activity_ticker = ActivityTicker()
                yield self.activity_ticker
                
            with Container(id="history-panel"):
                self.history_table = HistoryTable()
                yield self.history_table
        yield Footer()

    async def on_active_orders_table_manual_close_request(self, message: ActiveOrdersTable.ManualCloseRequest) -> None:
        order_id = message.order_id
        symbol = order_id.rsplit('_', 1)[0]
        self.activity_ticker.message = f"Requesting manual close for {order_id}..."
        
        ctx = kernel.contexts.get(self.engine_name)
        if ctx:
            pos = kernel.state_manager.get_position(self.engine_name, symbol)
            if pos:
                price = kernel.data_stream.latest_prices.get(symbol, pos['entry_price'])
                await kernel.close_position(self.engine_name, symbol, price, "MANUAL_CLOSE")
        
        await self.update_ui_sync()

    async def update_ui_sync(self):
        if not self.is_mounted:
            return

        try:
            stats = kernel.get_ui_state(self.engine_name)
            if not stats: return
            
            orders = kernel.get_ui_orders(self.engine_name)
            
            equity = stats.get('equity', 0)
            self.sub_title = f"BOT MONITOR | Engine: {self.engine_name} | Balance: ${equity:,.2f}"
            self.bot_stats.stats_data = stats
            
            activity = stats.get('latest_activity', "Engine processing...")
            self.activity_ticker.message = str(activity)
            
            # Transfer verbose queue to log history (only if new)
            # The StateManager appends to ui['verbose_queue']. We need to drain it.
            if self.engine_name in kernel.state_manager.state:
                v_queue = kernel.state_manager.state[self.engine_name]['ui']['verbose_queue']
                while v_queue:
                    msg = v_queue.pop(0)
                    self.log_history.append(msg)
            
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
                current_row_keys = list(self.active_orders_table.rows.keys())
                for row_key in current_row_keys:
                    row_id = str(row_key.value)
                    if row_id not in new_order_ids:
                        self.active_orders_table.remove_row(row_key)
            except Exception:
                pass

        except Exception as e:
            if hasattr(self, "activity_ticker") and self.activity_ticker:
                self.activity_ticker.message = f"[bold red]UI Error:[/] {str(e)}"

    async def on_mount(self) -> None:
        self.title = "🤖 KUCOIN TRADE BOT (KERNEL MODE)"
        self.set_interval(0.5, self.flush_logs)
        self.set_interval(1.0, self.update_ui_sync)
        self.activity_ticker.message = "Loading initial state..."
        self.active_orders_table.focus()
        
        # Start kernel background services once
        await kernel.start()
        
        # Load the default engine into memory
        engine_instance = self.available_engines[self.engine_idx]()
        await kernel.load_engine(engine_instance, self.execution_mode, self.current_market, config.TEST_INITIAL_BALANCE)
        self.engine_initialized = True
        self.activity_ticker.message = "Ready. Press 'S' to Start Engine."

    def flush_logs(self) -> None:
        while not log_queue.empty():
            msg = log_queue.get_nowait()
            self.log_history.append(msg)
            
        if len(self.log_history) > 1000:
            self.log_history = self.log_history[-1000:]
            
        if isinstance(self.screen, LogModal) and self.log_history:
            # simple live update
            pass

    def action_toggle_logs(self) -> None:
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
                asyncio.create_task(self.perform_engine_swap(selected_idx))

        self.push_screen(
            SelectionModal(f"Pilih Engine {self.current_market}", engine_options),
            handle_engine_selection
        )

    async def perform_engine_swap(self, new_idx: int):
        if self.is_processing: return
        self.is_processing = True
        
        try:
            if self.engine_initialized:
                kernel.unload_engine(self.engine_name)
                
            self.bot_running = False
            self.engine_idx = new_idx
            self.engine_name = self.available_engines[self.engine_idx]().name
            self.activity_ticker.message = f"Loading {self.engine_name}..."
            
            engine_instance = self.available_engines[self.engine_idx]()
            await kernel.load_engine(engine_instance, self.execution_mode, self.current_market, config.TEST_INITIAL_BALANCE)
            self.engine_initialized = True
            
            self.activity_ticker.message = f"Swapped to {self.engine_name}. Ready to start."
            self.save_bot_state()
        finally:
            self.is_processing = False

    async def action_toggle_bot(self) -> None:
        if self.is_processing: return
        self.is_processing = True
        
        try:
            if not self.bot_running:
                self.bot_running = True
                await kernel.start_engine(self.engine_name)
                self.activity_ticker.message = f"Engine Running ({self.execution_mode})..."
                self.run_bot_worker()
            else:
                self.bot_running = False
                await kernel.stop_engine(self.engine_name)
                self.activity_ticker.message = "Engine Stopped (Standby)."
        finally:
            self.is_processing = False

    async def action_toggle_mode(self) -> None:
        if self.bot_running:
            self.activity_ticker.message = "[bold red]Stop the bot first to change mode.[/bold red]"
            return
            
        self.execution_mode = 'TEST' if self.execution_mode == 'LIVE' else 'LIVE'
        kernel.update_engine_mode(self.engine_name, self.execution_mode)
        self.activity_ticker.message = f"Execution Mode switched to: [bold yellow]{self.execution_mode}[/]"
        self.sub_title = f"BOT MONITOR | Mode: {self.execution_mode}"

    @work
    async def run_bot_worker(self) -> None:
        while self.bot_running:
            try:
                await asyncio.sleep(1.0) 
            except Exception as e:
                self.log_history.append(f"[bold red]UI Worker Error:[/] {str(e)}")
                await asyncio.sleep(5)

    async def action_quit_app(self) -> None:
        self.bot_running = False
        await kernel.stop_engine(self.engine_name)
        await kernel.stop()
        await kucoin_client.close()
        await kucoin_futures_client.close()
        self.exit()

if __name__ == "__main__":
    TradingDashboard().run()
