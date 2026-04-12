from textual.app import ComposeResult
from textual.widgets import Static, DataTable, OptionList, Button, RichLog
from textual.screen import ModalScreen, Screen
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.message import Message
from rich.text import Text
from rich.panel import Panel
from rich.table import Table

class LogScreen(Screen):
    """A full-screen view to display system logs."""
    
    DEFAULT_CSS = """
    LogScreen {
        background: $surface;
        padding: 1;
    }

    #log-screen-container {
        width: 100%;
        height: 100%;
    }

    #log-screen-title {
        text-align: center;
        width: 100%;
        margin-bottom: 1;
        text-style: bold;
        color: $accent;
    }

    #log-screen-hint {
        text-align: center;
        width: 100%;
        margin-top: 1;
        color: $text-muted;
    }

    #log-screen-widget {
        height: 1fr;
        border: solid $primary;
        background: $background;
    }
    """

    def __init__(self, log_history: list[str]):
        super().__init__()
        self.log_history = log_history

    def compose(self) -> ComposeResult:
        with Container(id="log-screen-container"):
            yield Static("SYSTEM LOGS", id="log-screen-title")
            self.log_widget = RichLog(highlight=True, markup=True, id="log-screen-widget")
            yield self.log_widget
            yield Static("[Esc / L] Kembali ke Dashboard", id="log-screen-hint")

    def on_mount(self) -> None:
        for line in self.log_history:
            self.log_widget.write(line)
        self.log_widget.scroll_end(animate=False)
        self.log_widget.focus()

    def on_key(self, event) -> None:
        if event.key == "escape" or event.key == "l":
            self.dismiss()

class SelectionModal(ModalScreen):
    """A centered modal for selecting an option from a list."""
    
    DEFAULT_CSS = """
    SelectionModal {
        align: center middle;
    }

    #modal-container {
        width: 40;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1;
    }

    #modal-title {
        text-align: center;
        width: 100%;
        margin-bottom: 1;
        text-style: bold;
    }

    #modal-hint {
        text-align: center;
        width: 100%;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, title: str, options: list):
        super().__init__()
        self.modal_title = title
        self.options = options # List of (label, value)

    def compose(self) -> ComposeResult:
        with Container(id="modal-container"):
            yield Static(self.modal_title, id="modal-title")
            yield OptionList(*[opt[0] for opt in self.options], id="option-list")
            yield Static("[Enter] Pilih  [Esc] Batal", id="modal-hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        selected_value = self.options[event.option_index][1]
        self.dismiss(selected_value)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            # Direct dismissal using the highlighted index if the message wasn't caught
            option_list = self.query_one(OptionList)
            if option_list.highlighted is not None:
                selected_value = self.options[option_list.highlighted][1]
                self.dismiss(selected_value)

class BotStats(Static):
    """Widget to display account summary and bot performance stats."""
    
    stats_data = reactive({}, always_update=True)

    def render(self) -> Panel:
        if not self.stats_data:
            return Panel("Waiting for bot data...", title="[bold blue]Bot Statistics[/bold blue]", border_style="dim")
            
        equity = self.stats_data.get('equity', 0)
        mode = self.stats_data.get('mode', 'TEST')
        active_pos_count = self.stats_data.get('active_pos_count', 0)
        
        # PnL Metrics
        total_pnl = self.stats_data.get('total_pnl', 0)
        win_rate = self.stats_data.get('win_rate', 0)
        trade_count = self.stats_data.get('trade_count', 0)
        daily_pnl = self.stats_data.get('daily_pnl', 0)
        weekly_pnl = self.stats_data.get('weekly_pnl', 0)
        monthly_pnl = self.stats_data.get('monthly_pnl', 0)
        yearly_pnl = self.stats_data.get('yearly_pnl', 0)
        next_reset_in = self.stats_data.get('next_reset_in', '00:00:00')
        
        def fmt_pnl(val: float) -> str:
            color = "green" if val >= 0 else "red"
            return f"[{color}]${val:,.2f}[/]"

        # Create a clean info table for the panel
        table = Table.grid(expand=True)
        table.add_column(style="bold cyan")
        table.add_column(justify="right")
        
        table.add_row("Execution Mode", f"[{'yellow' if mode == 'LIVE' else 'green'}]{mode}[/]")
        table.add_row("Total Balance", f"${equity:,.2f}")
        table.add_row("Active Orders", str(active_pos_count))
        table.add_row("Trade Count", str(trade_count))
        table.add_row("Win Rate", f"{win_rate:.2f}%")
        table.add_row("Total PnL", fmt_pnl(total_pnl))
        table.add_row("Daily PnL", fmt_pnl(daily_pnl))
        table.add_row("Weekly PnL", fmt_pnl(weekly_pnl))
        table.add_row("Monthly PnL", fmt_pnl(monthly_pnl))
        table.add_row("Yearly PnL", fmt_pnl(yearly_pnl))
        table.add_row("Next Daily Reset", f"[dim]{next_reset_in}[/]")
        
        return Panel(table, title="[bold blue]Bot Status[/bold blue]", border_style="blue", padding=(0, 1))

class EnginePipeline(Static):
    """Widget to visualize the current transaction phase of the engine."""
    
    data = reactive({}, always_update=True)

    def render(self) -> Panel:
        if not self.data:
            return Panel("Engine Idle", title="[bold magenta]Live Pipeline[/bold magenta]")
            
        current = self.data.get('current_phase', 'IDLE')
        msg = self.data.get('status_message', '...')
        
        # Define Phases and Icons
        phases = [
            ("SCAN", "🔍"), ("DATA", "📥"), ("ANALYZE", "📊"), 
            ("RISK", "🛡️"), ("EXEC", "🚀"), ("SYNC", "✅")
        ]
        
        pipeline_line = ""
        for i, (code, icon) in enumerate(phases):
            is_active = (current == code)
            # Use bright colors for active, dim for others
            color = "bold green blink" if is_active else "dim"
            pipeline_line += f"[{color}]{icon} {code}[/]"
            if i < len(phases) - 1:
                pipeline_line += " [dim]→[/] "

        content = Text.assemble(
            Text.from_markup(pipeline_line),
            "\n\n",
            Text(f"Status: {msg}", style="italic cyan")
        )
        
        return Panel(content, title="[bold magenta]Live Pipeline[/bold magenta]", border_style="magenta")

class ActivityTicker(Static):
    """A static one-line activity ticker with a spinning animation."""
    
    message = reactive("Engine Idle", always_update=True)
    
    # Spinner frames for the animation
    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    
    def on_mount(self) -> None:
        self.frame_idx = 0
        # Fast interval for smooth spinning
        self.set_interval(0.1, self.update_spinner)

    def update_spinner(self) -> None:
        self.frame_idx = (self.frame_idx + 1) % len(self.SPINNER_FRAMES)
        self.refresh()

    def render(self) -> Text:
        spinner = self.SPINNER_FRAMES[self.frame_idx]
        msg = str(self.message) if self.message is not None else "Engine Idle"
        return Text.assemble(
            (f" {spinner} ", "bold cyan"),
            (msg, "white")
        )

class ActiveOrdersTable(DataTable):
    """Table to show real-time metrics for active orders/positions."""
    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.col_keys = self.add_columns("Symbol", "Type", "Size (USD)", "Entry", "Current", "SL", "TP", "PnL")

    async def on_key(self, event) -> None:
        if event.key == "c":
            # Direct access to coordinate is more reliable than row index for precision
            coord = self.cursor_coordinate
            if coord:
                try:
                    row_key, _ = self.coordinate_to_cell_key(coord)
                    order_id = str(row_key.value)
                    # Stop the key event so it doesn't trigger other things
                    event.stop()
                    # Tell the dashboard which SPECIFIC order ID to close
                    self.post_message(self.ManualCloseRequest(order_id))
                except Exception:
                    pass

    class ManualCloseRequest(Message):
        def __init__(self, order_id: str):
            super().__init__()
            self.order_id = order_id

    def update_order_data(self, order_id: str, symbol: str, side: str, size: float, entry: float, current: float, sl: float, tp: float, pnl: float):
        # Style logic
        side_text = Text(side)
        if "LONG" in side or "BUY" in side: side_text.stylize("bold green")
        elif "SHORT" in side or "SELL" in side: side_text.stylize("bold red")
        else: side_text.stylize("dim")

        pnl_text = Text(f"{'+' if pnl >= 0 else '-'}${abs(pnl):,.2f}")
        if pnl > 0: pnl_text.stylize("bold green")
        elif pnl < 0: pnl_text.stylize("bold red")

        sl_str = f"${sl:,.2f}" if sl > 0 else "N/A"
        tp_str = f"${tp:,.2f}" if tp > 0 else "N/A"

        size_usd = size * current
        row_data = [
            symbol, side_text, f"${size_usd:,.2f}", f"${entry:,.2f}", f"${current:,.2f}",
            sl_str, tp_str, pnl_text
        ]
        
        try:
            self.get_row_index(order_id)
            for i, col_key in enumerate(self.col_keys):
                self.update_cell(order_id, col_key, row_data[i])
        except Exception: 
            self.add_row(*row_data, key=order_id)

class HistoryTable(DataTable):
    """Table to show historical closed orders."""
    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_columns("Time", "Symbol", "Side", "Size (USD)", "PnL", "Max PnL", "Min PnL", "Reason")

    def add_history_entry(self, time_str: str, symbol: str, side: str, amount: float, exit_price: float, pnl: float, max_pnl: float, min_pnl: float, reason: str):
        side_text = Text(side)
        if "LONG" in side or "BUY" in side: side_text.stylize("bold green")
        elif "SHORT" in side or "SELL" in side: side_text.stylize("bold red")
        
        def fmt_pnl(val: float) -> Text:
            txt = Text(f"{'+' if val >= 0 else '-'}${abs(val):,.2f}")
            if val > 0: txt.stylize("bold green")
            elif val < 0: txt.stylize("bold red")
            return txt

        pnl_text = fmt_pnl(pnl)
        max_pnl_text = fmt_pnl(max_pnl)
        min_pnl_text = fmt_pnl(min_pnl)
        
        size_usd = amount * exit_price
        
        # We don't use key here because we might have multiple entries for same symbol
        self.add_row(
            time_str, symbol, side_text, f"${size_usd:,.2f}", pnl_text, max_pnl_text, min_pnl_text, reason
        )
