from core.logger import logger
from core.config import config
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

class CircuitBreaker:
    """
    Monitors overall account health and multi-timeframe PNL.
    Resets baselines at 07:00 AM on respective boundaries.
    """
    def __init__(self, max_daily_drawdown_pct: float = None):
        self.max_daily_drawdown_pct = max_daily_drawdown_pct if max_daily_drawdown_pct is not None else config.CIRCUIT_BREAKER_PCT # e.g., 0.05 (5%)
        self.is_tripped = False
        
        # Baselines
        self.baseline_daily: Optional[float] = None
        self.baseline_weekly: Optional[float] = None
        self.baseline_monthly: Optional[float] = None
        self.baseline_yearly: Optional[float] = None
        
        # Last Reset Times
        self.last_reset_daily: Optional[datetime] = None
        self.last_reset_weekly: Optional[datetime] = None
        self.last_reset_monthly: Optional[datetime] = None
        self.last_reset_yearly: Optional[datetime] = None

    def _get_reset_boundary(self, timeframe: str) -> datetime:
        """Returns the PREVIOUS 07:00 AM boundary for a given timeframe."""
        now = datetime.now()
        
        # Baseline is always anchored at 07:00:00
        if timeframe == 'daily':
            anchor = now.replace(hour=7, minute=0, second=0, microsecond=0)
            if now < anchor:
                anchor -= timedelta(days=1)
            return anchor
            
        elif timeframe == 'weekly':
            # Monday is 0
            days_since_monday = now.weekday()
            anchor = (now - timedelta(days=days_since_monday)).replace(hour=7, minute=0, second=0, microsecond=0)
            if now < anchor:
                anchor -= timedelta(days=7)
            return anchor
            
        elif timeframe == 'monthly':
            anchor = now.replace(day=1, hour=7, minute=0, second=0, microsecond=0)
            if now < anchor:
                # Go back to 1st of previous month
                if now.month == 1:
                    anchor = anchor.replace(year=now.year-1, month=12)
                else:
                    anchor = anchor.replace(month=now.month-1)
            return anchor
            
        elif timeframe == 'yearly':
            anchor = now.replace(month=1, day=1, hour=7, minute=0, second=0, microsecond=0)
            if now < anchor:
                anchor = anchor.replace(year=now.year-1)
            return anchor
            
        return now

    def update_equity(self, current_equity: float, engine_name: str = "Unknown") -> bool:
        """
        Checks current equity against baselines and handles resets.
        Returns True if trading is ALLOWED, False if Circuit Breaker is TRIPPED.
        """
        now = datetime.now()
        changed = False
        
        # 1. Handle Resets & Initializations
        # Daily
        daily_boundary = self._get_reset_boundary('daily')
        if self.baseline_daily is None or (self.last_reset_daily and self.last_reset_daily < daily_boundary):
            self.baseline_daily = current_equity
            self.last_reset_daily = now
            self.is_tripped = False # Reset trip status on daily reset
            logger.info(f"[{engine_name}] Daily baseline reset: ${self.baseline_daily:,.2f}")
            changed = True

        # Weekly
        weekly_boundary = self._get_reset_boundary('weekly')
        if self.baseline_weekly is None or (self.last_reset_weekly and self.last_reset_weekly < weekly_boundary):
            self.baseline_weekly = current_equity
            self.last_reset_weekly = now
            logger.info(f"[{engine_name}] Weekly baseline reset: ${self.baseline_weekly:,.2f}")
            changed = True

        # Monthly
        monthly_boundary = self._get_reset_boundary('monthly')
        if self.baseline_monthly is None or (self.last_reset_monthly and self.last_reset_monthly < monthly_boundary):
            self.baseline_monthly = current_equity
            self.last_reset_monthly = now
            logger.info(f"[{engine_name}] Monthly baseline reset: ${self.baseline_monthly:,.2f}")
            changed = True

        # Yearly
        yearly_boundary = self._get_reset_boundary('yearly')
        if self.baseline_yearly is None or (self.last_reset_yearly and self.last_reset_yearly < yearly_boundary):
            self.baseline_yearly = current_equity
            self.last_reset_yearly = now
            logger.info(f"[{engine_name}] Yearly baseline reset: ${self.baseline_yearly:,.2f}")
            changed = True

        # 2. Risk Check (Daily Drawdown)
        if self.is_tripped:
            return False
            
        drawdown_pct = (self.baseline_daily - current_equity) / self.baseline_daily
        if drawdown_pct >= self.max_daily_drawdown_pct:
            self.is_tripped = True
            logger.critical(f"[{engine_name}] CIRCUIT BREAKER TRIPPED! Drawdown ({drawdown_pct*100:.2f}%) exceeded limit.")
            return False
            
        return True

    async def load_baselines(self, engine_name: str) -> Optional[float]:
        """Load equity baselines and current equity from database."""
        try:
            from core.database import async_session
            from models.trade_history import EquityBaseline
            from sqlalchemy.future import select
            
            async with async_session() as session:
                stmt = select(EquityBaseline).where(EquityBaseline.engine_name == engine_name)
                result = await session.execute(stmt)
                record = result.scalars().first()
                
                if record:
                    self.baseline_daily = record.daily
                    self.baseline_weekly = record.weekly
                    self.baseline_monthly = record.monthly
                    self.baseline_yearly = record.yearly
                    self.last_reset_daily = record.last_reset_daily
                    self.last_reset_weekly = record.last_reset_weekly
                    self.last_reset_monthly = record.last_reset_monthly
                    self.last_reset_yearly = record.last_reset_yearly
                    logger.info(f"[{engine_name}] Equity baselines loaded from database.")
                    return record.current_equity
                return None
        except Exception as e:
            logger.error(f"Failed to load baselines for {engine_name}: {e}")
            return None

    async def save_baselines(self, engine_name: str, current_equity: float):
        """Save current equity and baselines to database."""
        try:
            from core.database import async_session
            from models.trade_history import EquityBaseline
            from sqlalchemy.future import select
            
            async with async_session() as session:
                stmt = select(EquityBaseline).where(EquityBaseline.engine_name == engine_name)
                result = await session.execute(stmt)
                record = result.scalars().first()
                
                if record:
                    record.current_equity = current_equity
                    record.daily = self.baseline_daily
                    record.weekly = self.baseline_weekly
                    record.monthly = self.baseline_monthly
                    record.yearly = self.baseline_yearly
                    record.last_reset_daily = self.last_reset_daily
                    record.last_reset_weekly = self.last_reset_weekly
                    record.last_reset_monthly = self.last_reset_monthly
                    record.last_reset_yearly = self.last_reset_yearly
                else:
                    new_record = EquityBaseline(
                        engine_name=engine_name,
                        current_equity=current_equity,
                        daily=self.baseline_daily,
                        weekly=self.baseline_weekly,
                        monthly=self.baseline_monthly,
                        yearly=self.baseline_yearly,
                        last_reset_daily=self.last_reset_daily,
                        last_reset_weekly=self.last_reset_weekly,
                        last_reset_monthly=self.last_reset_monthly,
                        last_reset_yearly=self.last_reset_yearly
                    )
                    session.add(new_record)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to save baselines for {engine_name}: {e}")


    def get_pnl_stats(self, current_equity: float) -> Dict[str, Any]:
        """Returns all timeframe PnL stats and reset timers."""
        # ENSURE: No side-effects here. Use local variables if baselines are not loaded yet.
        bd = self.baseline_daily if self.baseline_daily is not None else current_equity
        bw = self.baseline_weekly if self.baseline_weekly is not None else current_equity
        bm = self.baseline_monthly if self.baseline_monthly is not None else current_equity
        by = self.baseline_yearly if self.baseline_yearly is not None else current_equity
        
        # Calculate Next Daily Reset for the timer
        next_reset = self._get_reset_boundary('daily') + timedelta(days=1)
        time_until_reset = next_reset - datetime.now()
        hours, remainder = divmod(time_until_reset.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        return {
            'daily_pnl': current_equity - bd,
            'weekly_pnl': current_equity - bw,
            'monthly_pnl': current_equity - bm,
            'yearly_pnl': current_equity - by,
            'next_reset_in': f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        }

circuit_breaker = CircuitBreaker()
