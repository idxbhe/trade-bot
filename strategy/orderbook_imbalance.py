import pandas as pd
from typing import Dict, Any
from strategy.base_strategy import BaseStrategy

class OrderBookImbalanceStrategy(BaseStrategy):
    """
    High-Frequency Futures Scalping Strategy based on Order Book Imbalance (OBI).
    Identifies aggressive smart money positioning by comparing the volume of Bids vs Asks 
    at the top levels of the limit order book.
    """
    def __init__(self, depth_levels: int = 10, imbalance_threshold: float = 0.70):
        super().__init__("OBI_Scalper")
        self.depth_levels = depth_levels
        self.imbalance_threshold = imbalance_threshold
        
        if self.imbalance_threshold < 0.5:
             # A threshold below 0.5 would cause both BUY and SELL logic to overlap or be noisy.
             # Standard OBI usage expects threshold > 0.5.
             from core.logger import logger
             logger.warning(f"OrderBookImbalanceStrategy: Threshold {imbalance_threshold} is below 0.5! This may lead to overlapping signals.")

    def generate_signal(self, df: pd.DataFrame, current_price: float) -> Dict[str, Any]:
        """
        This strategy relies purely on real-time order book data, not historical candles.
        Therefore, we don't use the df parameter directly for signals, but we implement
        a custom evaluate_orderbook method.
        """
        return {'signal': 'HOLD', 'reason': 'Use evaluate_orderbook instead.'}

    def evaluate_orderbook(self, orderbook: dict) -> Dict[str, Any]:
        """
        Calculates the Order Book Imbalance (OBI) at the top levels.
        
        Formula: OBI = Total Bid Volume / (Total Bid Volume + Total Ask Volume)
        - OBI near 1.0: Bullish (Bids dominate)
        - OBI near 0.0: Bearish (Asks dominate)
        - OBI near 0.5: Neutral
        
        Args:
            orderbook (dict): Dict containing 'bids' and 'asks' lists [[price, amount], ...]
            
        Returns:
            dict: Signal data containing 'signal', 'reason', and 'obi' score.
        """
        if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook:
            return {'signal': 'HOLD', 'reason': 'Missing orderbook data', 'obi': 0.5}

        bids = orderbook['bids'][:self.depth_levels]
        asks = orderbook['asks'][:self.depth_levels]

        # Calculate total base volume at the top N levels
        total_bid_vol = sum(amount for price, amount in bids)
        total_ask_vol = sum(amount for price, amount in asks)

        if total_bid_vol == 0 and total_ask_vol == 0:
            return {'signal': 'HOLD', 'reason': 'Empty orderbook', 'obi': 0.5}

        obi = total_bid_vol / (total_bid_vol + total_ask_vol)

        if obi >= self.imbalance_threshold:
            # Massive buy walls / aggressive buyers
            return {
                'signal': 'BUY', 
                'reason': f'High Buy Imbalance ({obi*100:.1f}%)', 
                'obi': obi
            }
        elif obi <= (1.0 - self.imbalance_threshold):
            # Massive sell walls / aggressive sellers
            return {
                'signal': 'SELL', 
                'reason': f'High Sell Imbalance ({(1-obi)*100:.1f}%)', 
                'obi': obi
            }

        return {
            'signal': 'HOLD', 
            'reason': f'Balanced Orderbook ({obi*100:.1f}% Bids)', 
            'obi': obi
        }
