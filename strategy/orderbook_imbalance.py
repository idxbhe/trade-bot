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

    def generate_signal(self, df: pd.DataFrame, current_price: float) -> Dict[str, Any]:
        """
        This strategy relies purely on real-time order book data, not historical candles.
        Therefore, we don't use the df parameter directly for signals, but we implement
        a custom evaluate_orderbook method.
        """
        return {'signal': 'HOLD', 'reason': 'Use evaluate_orderbook instead.'}

    def evaluate_orderbook(self, orderbook: dict) -> Dict[str, Any]:
        """
        Evaluates the ccxt orderbook structure.
        orderbook['bids'] -> [[price, amount], ...]
        orderbook['asks'] -> [[price, amount], ...]
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
                'signal': 'LONG', 
                'reason': f'High Buy Imbalance ({obi*100:.1f}%)', 
                'obi': obi
            }
        elif obi <= (1.0 - self.imbalance_threshold):
            # Massive sell walls / aggressive sellers
            return {
                'signal': 'SHORT', 
                'reason': f'High Sell Imbalance ({(1-obi)*100:.1f}%)', 
                'obi': obi
            }

        return {
            'signal': 'HOLD', 
            'reason': f'Balanced Orderbook ({obi*100:.1f}% Bids)', 
            'obi': obi
        }
