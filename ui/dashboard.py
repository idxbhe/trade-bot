import asyncio
import traceback
import time
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, Select
from textual.containers import Grid, Container, Vertical
from textual import work

from ui.components import WatchlistTable, BotStats
from core.logger import log_queue
from core.config import config
from exchange.kucoin import kucoin_client

# Engines Registry
from engines.hybrid_engine_v1 import HybridEngineV1
from engines.scanner_engine import ScannerOnlyEngine

class TradingDashboard(App):
    """
    Host Dashboard with Explicit Engine Selection UI.
    Select your trading 'brain' from the dropdown menu.
    """
    
    CSS = """
    Grid {
        grid-size: 2 2;
        grid-columns: 1.5fr 1fr;
        grid-rows: 1.5fr 1fr;
        padding: 1;
        background: $surface;
    }
    
    #market-panel, #stats-container, #log-panel {
        height: 100%;
        width: 100%;
    }
    
    #market-panel { border: solid green; }
    
    #stats-container { 
        border: solid blue; 
        padding: 1;
    }
    
    #engine-selector {
        margin-bottom: 1;
    }
    
    #log-panel { column-span: 2; border: solid white; }
    
    RichLog { height: 100%; }
    """
    
    BINDINGS = [
        ("q", "quit_app", "Quit"),
        ("s", "toggle_bot", "Start/Stop Bot"),
        ("e", "focus_selector", "Select Engine")
    ]

    def __init__(self):
        super().__init__()
        self.bot_running = False
        
        # Available Engines Registry
        self.available_engines = [HybridEngineV1, ScannerOnlyEngine]
        self.engine_idx = 0
        
        # Load Initial Engine
        self.engine = self.available_engines[self.engine_idx]()
        self.engine_initialized = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Grid():
            with Container(id="market-panel"):
                self.watchlist = WatchlistTable()
                yield self.watchlist
            
            with Vertical(id="stats-container"):
                # Engine Selector Dropdown
                engine_options = [(eng().name, i) for i, eng in enumerate(self.available_engines)]
                self.selector = Select(engine_options, prompt="Choose Trading Engine", id="engine-selector", value=0)
                yield self.selector
                
                self.bot_stats = BotStats()
                yield self.bot_stats
                
            with Container(id="log-panel"):
                self.log_widget = RichLog(highlight=True, markup=True)
                yield self.log_widget
        yield Footer()

    async def on_select_changed(self, event: Select.Changed) -> None:
        """Handle engine selection from the dropdown UI with safety lock."""
        if self.bot_running:
            self.log_widget.write("[bold red]DENIED: Stop the bot first to change engine.[/bold red]")
            # Reset selector value to current engine index to reflect reality
            self.selector.value = self.engine_idx
            return

        if event.value is not None and event.value != self.engine_idx:
            await self.perform_engine_swap(event.value)

    async def perform_engine_swap(self, new_idx: int):
        """Logic to safely swap engines."""
        was_running = self.bot_running
        
        # 1. Stop current loop
        self.bot_running = False
        self.log_widget.write(f"[bold yellow]Swapping to engine: {self.available_engines[new_idx]().name}...[/bold yellow]")
        
        # 2. Shutdown old engine
        if self.engine_initialized:
            await self.engine.shutdown()
        
        # 3. Swap instance
        self.engine_idx = new_idx
        self.engine = self.available_engines[self.engine_idx]()
        self.engine_initialized = False
        
        # 4. Re-init if it was running
        if was_running:
            await self.engine.setup(kucoin_client, config)
            self.engine_initialized = True
            self.engine.start()
            self.bot_running = True
            self.run_bot_worker()
            
        self.log_widget.write(f"[bold green]Engine active: {self.engine.name}[/bold green]")

    async def update_ui_sync(self):
        """Fetch data from the current active engine with robust error handling."""
        try:
            stats = await self.engine.get_stats()
            symbols = await self.engine.get_active_symbols()
            
            mode_str = stats.get('mode', 'TEST')
            equity = stats.get('equity', 0)
            self.sub_title = f"BOT MONITOR | Engine: {self.engine.name} | Equity: ${equity:,.2f}"
            
            self.bot_stats.stats_data = stats
            
            if symbols:
                for item in symbols:
                    self.watchlist.update_symbol_data(
                        item['symbol'], item['price'], item['rsi'], item['adx'], 
                        item['signal'], item['position'], item['pnl']
                    )
        except Exception as e:
            self.log_widget.write(f"[dim red]UI Refresh Issue: {e}[/dim red]")

    def on_mount(self) -> None:
        self.title = "🤖 KUCOIN TRADE BOT"
        self.set_interval(0.5, self.flush_logs)
        self.set_interval(2.0, self.update_ui_sync)
        self.log_widget.write(f"[bold green]Host mounted. Ready to trade.[/bold green]")

    def action_focus_selector(self) -> None:
        """Focus the engine selector dropdown."""
        self.selector.focus()

    async def action_toggle_bot(self) -> None:
        if not self.bot_running:
            self.bot_running = True
            self.selector.disabled = True
            
            if not self.engine_initialized:
                await self.engine.setup(kucoin_client, config)
                self.engine_initialized = True
            self.engine.start()
            self.log_widget.write(f"[bold green]Starting {self.engine.name}...[/bold green]")
            self.run_bot_worker()
        else:
            self.bot_running = False
            self.engine.stop()
            self.selector.disabled = False
            self.log_widget.write(f"[bold red]Stopping {self.engine.name}...[/bold red]")

    def flush_logs(self) -> None:
        while not log_queue.empty():
            self.log_widget.write(log_queue.get_nowait())

    @work(exclusive=True)
    async def run_bot_worker(self) -> None:
        while self.bot_running:
            try:
                await self.engine.update()
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log_widget.write(f"[bold red]Engine Error: {e}[/bold red]")
                await asyncio.sleep(10)

    async def action_quit_app(self) -> None:
        self.bot_running = False
        if self.engine_initialized:
            await self.engine.shutdown()
        self.exit()

if __name__ == "__main__":
    TradingDashboard().run()
