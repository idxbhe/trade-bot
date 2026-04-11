from core.logger import logger
from core.config import config

class CircuitBreaker:
    """
    Pure Logic Circuit Breaker.
    Calculates if current equity violates the maximum allowed drawdown from a given baseline.
    """
    def __init__(self, max_daily_drawdown_pct: float = None):
        self.max_daily_drawdown_pct = max_daily_drawdown_pct if max_daily_drawdown_pct is not None else config.CIRCUIT_BREAKER_PCT
        self._is_tripped = False

    def is_tripped(self, current_equity: float, baseline: float) -> bool:
        """
        Checks current equity against the baseline.
        Returns True if Circuit Breaker is TRIPPED, False if trading is ALLOWED.
        """
        if self._is_tripped:
            return True
            
        if baseline is None or baseline <= 0:
            return False
            
        drawdown_pct = (baseline - current_equity) / baseline
        if drawdown_pct >= self.max_daily_drawdown_pct:
            self._is_tripped = True
            logger.critical(f"CIRCUIT BREAKER TRIPPED! Drawdown ({drawdown_pct*100:.2f}%) exceeded limit ({self.max_daily_drawdown_pct*100:.2f}%).")
            return True
            
        return False
        
    def reset(self):
        """Manually reset the tripped status (usually done at 07:00 AM by Framework)."""
        self._is_tripped = False
