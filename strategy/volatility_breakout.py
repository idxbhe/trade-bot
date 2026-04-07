import pandas as pd
from strategy.base_strategy import BaseStrategy
from data.indicators import Indicators

class VolatilityBreakoutStrategy(BaseStrategy):
    """
    Aggressive 1-Minute Scalping Strategy based on ATR and Bollinger Band Breakouts.
    Catches violent momentum expansions after periods of low volatility (squeeze).
    """
    def __init__(self):
        super().__init__("VolatilityBreakout_Scalper")
        
    def generate_signal(self, df: pd.DataFrame, current_price: float) -> dict:
        if df.empty or len(df) < 20:
            return {'signal': 'HOLD', 'reason': 'Not enough data'}
            
        # Ensure we have all necessary indicators
        if 'vol_sma_20' not in df.columns:
            # The engine will likely calculate it, but if not we do it here
            df['vol_sma_20'] = Indicators.calculate_sma(df, period=20, column='volume')
            
        last_row = df.iloc[-1]
        
        if pd.isna(last_row.get('bb_upper')) or pd.isna(last_row.get('vol_sma_20')):
            return {'signal': 'HOLD', 'reason': 'Calculating indicators...'}
            
        close = last_row['close']
        bb_upper = last_row['bb_upper']
        volume = last_row['volume']
        vol_sma = last_row['vol_sma_20']
        
        # 1. The Breakout & Volume Confirmation
        if close > bb_upper and volume > vol_sma:
            return {'signal': 'BUY', 'reason': 'High Vol Breakout above Upper BB'}
            
        # 2. Dynamic Take Profit (Closing back inside)
        if close < bb_upper:
            return {'signal': 'EXIT_LONG', 'reason': 'Price closed back inside BB'}
            
        return {'signal': 'HOLD', 'reason': 'Awaiting volatility breakout'}
