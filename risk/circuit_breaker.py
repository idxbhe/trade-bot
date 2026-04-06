from core.logger import logger
from core.config import config
from typing import Optional
from datetime import datetime, timedelta

class CircuitBreaker:
    """
    Monitors overall account health and daily PNL.
    Triggers a full system halt if drawdown exceeds the threshold (e.g., 5% in 24h).
    """
    def __init__(self):
        self.max_daily_drawdown_pct = config.CIRCUIT_BREAKER_PCT # e.g., 0.05 (5%)
        self.initial_daily_equity: Optional[float] = None
        self.last_reset_time: Optional[datetime] = None
        self.is_tripped = False
        
    def update_equity(self, current_equity: float) -> bool:
        """
        Checks current equity against the daily baseline.
        Returns True if trading is ALLOWED, False if Circuit Breaker is TRIPPED.
        """
        now = datetime.utcnow()
        
        # Initialize or reset the daily baseline at midnight UTC
        if self.initial_daily_equity is None or (self.last_reset_time and now.date() > self.last_reset_time.date()):
            self.initial_daily_equity = current_equity
            self.last_reset_time = now
            self.is_tripped = False
            logger.info(f"Circuit Breaker reset. New daily baseline equity: ${self.initial_daily_equity:,.2f}")
            return True
            
        if self.is_tripped:
            logger.error("SYSTEM HALTED: Circuit Breaker is currently TRIPPED. Manual intervention required.")
            return False
            
        # Calculate drawdown
        drawdown_pct = (self.initial_daily_equity - current_equity) / self.initial_daily_equity
        
        if drawdown_pct >= self.max_daily_drawdown_pct:
            self.is_tripped = True
            logger.critical(f"CIRCUIT BREAKER TRIPPED! Drawdown ({drawdown_pct*100:.2f}%) exceeded maximum allowed ({self.max_daily_drawdown_pct*100:.2f}%).")
            logger.critical(f"Initial Equity: ${self.initial_daily_equity:,.2f} | Current Equity: ${current_equity:,.2f}")
            return False
            
        return True

circuit_breaker = CircuitBreaker()
