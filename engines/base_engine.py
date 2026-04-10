from abc import ABC, abstractmethod
from typing import Dict, List, Any
from core.logger import logger

class BaseEngine(ABC):
    """
    Abstract Base Class for all Trading Engines (Plug-and-Play).
    Each engine must implement these methods to be compatible with the Dashboard.
    """
    # Standard Transaction Phases
    PHASE_IDLE = "IDLE"
    PHASE_SCAN = "SCAN"
    PHASE_DATA = "DATA"
    PHASE_ANALYZE = "ANALYZE"
    PHASE_RISK = "RISK"
    PHASE_EXEC = "EXEC"
    PHASE_SYNC = "SYNC"

    def __init__(self, name: str):
        self.name = name
        self.is_running = False
        self.exchange = None
        self.config = None
        self.logger = logger
        self.current_phase = self.PHASE_IDLE
        self.status_message = "Engine initialized"
        self.verbose_queue = [] # Pretty logs for the UI
        self.latest_activity = "Engine ready" # Static status line
        self.order_history = [] # List of closed orders
        self.market = "Spot" # Default, updated in subclasses
        self.mode = "TEST" # Default, updated in subclasses
        self._is_updating = False
        self._cached_stats = None
        
        # Risk Management (to be initialized by subclasses)
        self.risk_manager = None
        self.circuit_breaker = None

    async def save_order_history(self, record: dict):
        try:
            from core.database import async_session
            from models.trade_history import TradeHistory
            
            async with async_session() as session:
                new_record = TradeHistory(
                    engine_name=self.name,
                    market=self.market,
                    mode=self.mode,
                    symbol=record.get('symbol'),
                    side=record.get('side'),
                    amount=record.get('amount'),
                    entry_price=record.get('entry'),
                    exit_price=record.get('exit'),
                    pnl=record.get('pnl'),
                    max_pnl=record.get('max_pnl'),
                    min_pnl=record.get('min_pnl'),
                    reason=record.get('reason'),
                    time=record.get('time')
                )
                session.add(new_record)
                await session.commit()
                self._cached_stats = None
        except Exception as e:
            self.logger.error(f"Failed to save order history for {self.name}: {e}")

    async def load_active_positions(self) -> Dict[str, dict]:
        """Load active positions from database on startup."""
        try:
            from core.database import async_session
            from models.trade_history import ActivePosition
            from sqlalchemy.future import select
            
            async with async_session() as session:
                stmt = select(ActivePosition).where(ActivePosition.engine_name == self.name)
                result = await session.execute(stmt)
                records = result.scalars().all()
                
                positions = {}
                for r in records:
                    positions[r.symbol] = {
                        'entry_price': r.entry_price,
                        'amount': r.amount,
                        'stop_loss': r.stop_loss,
                        'take_profit': r.take_profit,
                        'max_pnl': r.max_pnl,
                        'min_pnl': r.min_pnl
                    }
                return positions
        except Exception as e:
            self.logger.error(f"Failed to load active positions for {self.name}: {e}")
            return {}

    async def save_active_position(self, symbol: str, pos: dict, side: str = "LONG"):
        """Save or update an active position in the database."""
        try:
            from core.database import async_session
            from models.trade_history import ActivePosition
            from sqlalchemy.future import select
            
            async with async_session() as session:
                stmt = select(ActivePosition).where(
                    ActivePosition.engine_name == self.name,
                    ActivePosition.symbol == symbol
                )
                result = await session.execute(stmt)
                record = result.scalars().first()
                
                if record:
                    record.amount = pos['amount']
                    record.entry_price = pos['entry_price']
                    record.stop_loss = pos.get('stop_loss', 0.0)
                    record.take_profit = pos.get('take_profit', 0.0)
                    record.max_pnl = pos.get('max_pnl', 0.0)
                    record.min_pnl = pos.get('min_pnl', 0.0)
                else:
                    new_record = ActivePosition(
                        engine_name=self.name,
                        symbol=symbol,
                        side=side,
                        amount=pos['amount'],
                        entry_price=pos['entry_price'],
                        stop_loss=pos.get('stop_loss', 0.0),
                        take_profit=pos.get('take_profit', 0.0),
                        max_pnl=pos.get('max_pnl', 0.0),
                        min_pnl=pos.get('min_pnl', 0.0)
                    )
                    session.add(new_record)
                await session.commit()
        except Exception as e:
            self.logger.error(f"Failed to save active position {symbol} for {self.name}: {e}")

    async def remove_active_position(self, symbol: str):
        """Remove an active position from the database after closing."""
        try:
            from core.database import async_session
            from models.trade_history import ActivePosition
            from sqlalchemy.future import select
            
            async with async_session() as session:
                stmt = select(ActivePosition).where(
                    ActivePosition.engine_name == self.name,
                    ActivePosition.symbol == symbol
                )
                result = await session.execute(stmt)
                record = result.scalars().first()
                if record:
                    await session.delete(record)
                    await session.commit()
        except Exception as e:
            self.logger.error(f"Failed to remove active position {symbol} for {self.name}: {e}")

    def _push_log(self, category: str, icon: str, color: str, symbol: str, message: str):
        """Standardized log formatter for UI consistency."""
        # Update current state for the static UI
        self.current_phase = category
        
        # Format the static line: [CATEGORY] Symbol Message (But hide SCAN as requested)
        if category == "SCAN":
            self.latest_activity = f"{symbol:10} {message}"
        else:
            self.latest_activity = f"[{category:8}] {symbol:10} {message}"

        # Push to queue for the bottom history log (removing timestamps)
        formatted = f"[{color}]{icon} {category:8}[/] [bold]{symbol:10}[/] {message}"
        self.verbose_queue.append(formatted)

    def report_scan(self, symbol: str, msg: str = "Scanning..."):
        self._push_log("SCAN", "🔍", "yellow", symbol, msg)

    def report_analyze(self, symbol: str, msg: str):
        self._push_log("ANALYZE", "📊", "magenta", symbol, msg)

    def report_risk(self, symbol: str, msg: str):
        self._push_log("RISK", "🛡️", "orange3", symbol, msg)

    def report_execution(self, symbol: str, msg: str):
        self._push_log("EXEC", "🚀", "bold green", symbol, msg)

    def report_info(self, msg: str):
        self.latest_activity = msg
        self.verbose_queue.append(f"[blue]ℹ INFO[/] {msg}")

    async def get_historical_stats(self) -> Dict[str, Any]:
        """Calculate aggregate performance stats from the database."""
        if self._cached_stats is not None:
            return self._cached_stats
            
        try:
            from core.database import async_session
            from models.trade_history import TradeHistory
            from sqlalchemy.future import select
            from sqlalchemy import func
            from datetime import datetime, timedelta
            
            async with async_session() as session:
                # 1. Define Reset Boundaries (07:00 AM)
                now = datetime.now()
                
                daily_boundary = now.replace(hour=7, minute=0, second=0, microsecond=0)
                if now < daily_boundary: daily_boundary -= timedelta(days=1)
                
                weekly_boundary = (now - timedelta(days=now.weekday())).replace(hour=7, minute=0, second=0, microsecond=0)
                if now < weekly_boundary: weekly_boundary -= timedelta(days=7)
                
                monthly_boundary = now.replace(day=1, hour=7, minute=0, second=0, microsecond=0)
                if now < monthly_boundary:
                    if now.month == 1:
                        monthly_boundary = monthly_boundary.replace(year=now.year-1, month=12)
                    else:
                        monthly_boundary = monthly_boundary.replace(month=now.month-1)
                
                yearly_boundary = now.replace(month=1, day=1, hour=7, minute=0, second=0, microsecond=0)
                if now < yearly_boundary: yearly_boundary = yearly_boundary.replace(year=now.year-1)

                # 2. Get Lifetime Stats
                stmt = (
                    select(
                        func.sum(TradeHistory.pnl).label("total_pnl"),
                        func.count(TradeHistory.id).label("trade_count")
                    )
                    .where(TradeHistory.engine_name == self.name)
                    .where(TradeHistory.market == self.market)
                    .where(TradeHistory.mode == self.mode)
                )
                result = await session.execute(stmt)
                row = result.first()
                
                total_pnl = float(row.total_pnl) if row and row.total_pnl is not None else 0.0
                trade_count = int(row.trade_count) if row and row.trade_count is not None else 0
                
                # 3. Get Timeframe Stats (Closed PnL only)
                def get_pnl_stmt(boundary):
                    return select(func.sum(TradeHistory.pnl)).where(
                        TradeHistory.engine_name == self.name,
                        TradeHistory.market == self.market,
                        TradeHistory.mode == self.mode,
                        TradeHistory.created_at >= boundary
                    )
                
                today_pnl = (await session.execute(get_pnl_stmt(daily_boundary))).scalar() or 0.0
                week_pnl = (await session.execute(get_pnl_stmt(weekly_boundary))).scalar() or 0.0
                month_pnl = (await session.execute(get_pnl_stmt(monthly_boundary))).scalar() or 0.0
                year_pnl = (await session.execute(get_pnl_stmt(yearly_boundary))).scalar() or 0.0

                # 4. Get Win Rate
                win_stmt = (
                    select(func.count(TradeHistory.id))
                    .where(TradeHistory.engine_name == self.name)
                    .where(TradeHistory.market == self.market)
                    .where(TradeHistory.mode == self.mode)
                    .where(TradeHistory.pnl > 0)
                )
                win_result = await session.execute(win_stmt)
                wins = win_result.scalar() or 0
                
                win_rate = (wins / trade_count * 100) if trade_count > 0 else 0.0
                
                self._cached_stats = {
                    "historical_pnl": total_pnl,
                    "today_pnl": today_pnl,
                    "week_pnl": week_pnl,
                    "month_pnl": month_pnl,
                    "year_pnl": year_pnl,
                    "trade_count": trade_count,
                    "win_rate": win_rate
                }
                return self._cached_stats
        except Exception as e:
            self.logger.error(f"Error fetching historical stats for {self.name}: {e}")
            return {
                "historical_pnl": 0.0,
                "today_pnl": 0.0,
                "week_pnl": 0.0,
                "month_pnl": 0.0,
                "year_pnl": 0.0,
                "trade_count": 0,
                "win_rate": 0.0
            }


    @abstractmethod
    async def setup(self, exchange_client: Any, config: Any):
        """Initialize the engine with resources and settings."""
        pass

    @abstractmethod
    async def update(self):
        """
        The main 'heartbeat' of the engine.
        Called periodically to perform scanning, filtering, and execution.
        """
        pass

    @abstractmethod
    def get_total_equity(self) -> float:
        """Return the current total equity (cash + open positions)."""
        pass

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        """Return performance and account status data for the UI."""
        pass

    @abstractmethod
    async def get_active_orders(self) -> List[Dict[str, Any]]:
        """Return list of active orders/positions for the UI."""
        pass

    async def get_order_history(self) -> List[Dict[str, Any]]:
        """Return history of closed orders from the database."""
        try:
            from core.database import async_session
            from models.trade_history import TradeHistory
            from sqlalchemy.future import select
            
            async with async_session() as session:
                result = await session.execute(
                    select(TradeHistory)
                    .where(TradeHistory.engine_name == self.name)
                    .where(TradeHistory.market == self.market)
                    .where(TradeHistory.mode == self.mode)
                    .order_by(TradeHistory.id.asc())
                )
                records = result.scalars().all()
                
                history = []
                for r in records:
                    history.append({
                        'time': r.time,
                        'symbol': r.symbol,
                        'side': r.side,
                        'amount': r.amount,
                        'entry': r.entry_price,
                        'exit': r.exit_price,
                        'pnl': r.pnl,
                        'max_pnl': r.max_pnl,
                        'min_pnl': r.min_pnl,
                        'reason': r.reason
                    })
                return history
        except Exception as e:
            self.logger.error(f"Error fetching order history for {self.name}: {e}")
            return []

    @abstractmethod
    async def close_position(self, order_id: str):
        """Manually close a specific position by its ID."""
        pass

    @abstractmethod
    async def shutdown(self):
        """Safely close positions or cancel orders before stopping."""
        pass

    def start(self):
        """Generic start flag."""
        self.is_running = True
        self.logger.info(f"Engine '{self.name}' started.")

    def stop(self):
        """Generic stop flag."""
        self.is_running = False
        self.logger.info(f"Engine '{self.name}' stopped.")


    async def clear_all_data(self):
        """Reset trade history, active positions, and equity baselines for this engine."""
        try:
            from core.database import async_session
            from models.trade_history import TradeHistory, ActivePosition, EquityBaseline
            from sqlalchemy import delete

            async with async_session() as session:
                # 1. Clear Trade History
                await session.execute(
                    delete(TradeHistory).where(TradeHistory.engine_name == self.name)
                )
                # 2. Clear Active Positions
                await session.execute(
                    delete(ActivePosition).where(ActivePosition.engine_name == self.name)
                )
                # 3. Clear Equity Baselines
                await session.execute(
                    delete(EquityBaseline).where(EquityBaseline.engine_name == self.name)
                )
                await session.commit()

            self.active_positions = {}
            if self.circuit_breaker:
                self.circuit_breaker.baseline_daily = None
                self.circuit_breaker.baseline_weekly = None
                self.circuit_breaker.baseline_monthly = None
                self.circuit_breaker.baseline_yearly = None
                self.circuit_breaker.is_tripped = False

            self._cached_stats = None
            self.logger.info(f"All data cleared and status reset for engine '{self.name}'.")
        except Exception as e:
            self.logger.error(f"Failed to clear data for {self.name}: {e}")

