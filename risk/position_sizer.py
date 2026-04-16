import math
from typing import Dict, Any, Tuple
from core.logger import logger
from core.config import config

class PositionSizer:
    """
    Handles calculating the correct position size based on risk parameters
    and dynamic stop losses based on ATR (Average True Range).
    """
    def __init__(self, max_risk_pct: float = None, atr_multiplier: float = None):
        self.max_risk_pct = max_risk_pct if max_risk_pct is not None else config.MAX_RISK_PER_TRADE_PCT # e.g., 0.01 (1%)
        self.atr_multiplier = atr_multiplier if atr_multiplier is not None else 1.5 # Default 1.5x - 2.0x

    def calculate_stop_loss(self, entry_price: float, atr_value: float, is_long: bool = True) -> float:
        """
        Calculate a dynamic stop loss using ATR.
        For LONG: Entry - (ATR * Multiplier)
        For SHORT: Entry + (ATR * Multiplier)
        """
        if math.isnan(atr_value) or atr_value <= 0:
            logger.warning(f"Invalid ATR value ({atr_value}). Using a default 2% stop loss.")
            return entry_price * 0.98 if is_long else entry_price * 1.02

        atr_distance = atr_value * self.atr_multiplier
        
        if is_long:
            sl_price = entry_price - atr_distance
        else:
            sl_price = entry_price + atr_distance
            
        return max(0.0001, sl_price) # Prevent negative or zero stop loss

    def calculate_position_size(self, account_equity: float, entry_price: float, stop_loss_price: float, leverage: int = 1) -> Tuple[float, float]:
        """
        Formula: Position Size = (Account Equity * Risk%) / (Entry Price - Stop Loss Price)
        Returns: (size_in_base_asset, total_risk_usd)
        """
        if account_equity <= 0:
            logger.error("Account equity is zero or negative. Cannot size position.")
            return 0.0, 0.0

        # Leverage validation
        if leverage < 1:
            leverage = 1

        risk_amount_usd = account_equity * self.max_risk_pct
        price_risk_per_unit = abs(entry_price - stop_loss_price)
        
        if price_risk_per_unit <= 0:
             logger.warning("Entry price and Stop Loss are too close. Cannot calculate size safely.")
             return 0.0, 0.0
             
        size_in_base_asset = risk_amount_usd / price_risk_per_unit
        
        # Check if the calculated size exceeds total buying power (including leverage)
        max_affordable_size = (account_equity * leverage) / entry_price
        
        if size_in_base_asset > max_affordable_size:
            logger.warning(f"Calculated size ({size_in_base_asset:.4f}) exceeds account buying power. Capping at max affordable ({max_affordable_size:.4f}).")
            size_in_base_asset = max_affordable_size * 0.99 # 99% to leave room for fees
            
        actual_risk_usd = size_in_base_asset * price_risk_per_unit
        
        return size_in_base_asset, actual_risk_usd

position_sizer = PositionSizer()
