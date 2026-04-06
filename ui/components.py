from textual.app import ComposeResult
from textual.widgets import Static, DataTable
from textual.reactive import reactive
from rich.text import Text
from rich.panel import Panel
from rich.table import Table

class BotStats(Static):
    """Widget to display account summary and bot performance stats."""
    
    stats_data = reactive({}, always_update=True)

    def render(self) -> Panel:
        if not self.stats_data:
            return Panel("Waiting for bot data...", title="[bold blue]Bot Statistics[/bold blue]")
            
        equity = self.stats_data.get('equity', 0)
        mode = self.stats_data.get('mode', 'TEST')
        active_pos_count = self.stats_data.get('active_pos_count', 0)
        total_pnl = self.stats_data.get('total_pnl', 0)
        
        pnl_color = "green" if total_pnl >= 0 else "red"
        
        # Create a clean info table for the panel
        table = Table.grid(expand=True)
        table.add_column(style="bold")
        table.add_column(justify="right")
        
        table.add_row("Execution Mode:", f"[{'yellow' if mode == 'LIVE' else 'green'}]{mode}[/]")
        table.add_row("Total Equity:", f"${equity:,.2f}")
        table.add_row("Active Positions:", str(active_pos_count))
        table.add_row("Session PnL:", f"[{pnl_color}]${total_pnl:,.2f}[/]")
        
        return Panel(table, title="[bold blue]Bot Status[/bold blue]", border_style="blue")

class WatchlistTable(DataTable):
    """Table to show real-time metrics for watched symbols."""
    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.col_keys = self.add_columns("Symbol", "Price", "RSI (14)", "ADX (14)", "Signal", "Position", "PnL")
        
    def update_symbol_data(self, symbol: str, price: float, rsi: float, adx: float, signal: str, pos_str: str = "None", pnl_str: str = "$0.00"):
        # Style logic
        signal_text = Text(signal)
        if "BUY" in signal: signal_text.stylize("bold green")
        elif "EXIT" in signal: signal_text.stylize("bold red")
        else: signal_text.stylize("dim")

        pnl_text = Text(pnl_str)
        if pnl_str.startswith("+"): pnl_text.stylize("bold green")
        elif pnl_str.startswith("-"): pnl_text.stylize("bold red")

        row_data = [
            symbol, f"${price:,.2f}", f"{rsi:.1f}", f"{adx:.1f}",
            signal_text, pos_str, pnl_text
        ]
        
        try:
            self.get_row_index(symbol)
            for i, col_key in enumerate(self.col_keys):
                self.update_cell(symbol, col_key, row_data[i])
        except Exception: 
            self.add_row(*row_data, key=symbol)
